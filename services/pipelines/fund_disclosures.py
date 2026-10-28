"""Monthly fund portfolios, benchmark constituent weights and expense ratios -> fund_holdings,
index_constituents, fund_expense_ratios. Idempotent.

Rows come from a source adapter (services/pipelines/sources.py); this module never downloads. AMCs disclose
one portfolio per scheme; adapters must repeat it under every plan's amfi_code (direct and regular).
Usage: uv run python -m services.pipelines.fund_disclosures --month 2026-09 [--source licensed_vendor]
"""

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

import psycopg

from db import DATABASE_URL

ISIN = re.compile(r"^IN[A-Z0-9]{10}$")


@dataclass
class Parsed:
    """Validated rows for one dataset. holdings: (amfi_code, month, isin, weight_pct, market_value | None);
    constituents: (index_name, month, isin, weight_pct); expense ratios: (amfi_code, as_of, ter_pct).
    month is the first day of the disclosure month; weights are % of net assets / index (0-100]."""
    rows: list[tuple] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)


def _num(v) -> Decimal | None:
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _month_ok(d) -> bool:
    return isinstance(d, date) and d.day == 1


def build_holdings(records) -> Parsed:
    """Fund weights may be negative (short derivative legs) but never beyond +-100%; ISIN must be Indian format
    (cash/TREPS lines without an ISIN are simply left out: the uninvested remainder is treated as cash)."""
    out, seen = Parsed(), set()
    for code, month, isin, weight, mv in records:
        w = _num(weight)
        if not (isinstance(code, str) and code.isdigit()):
            reason = "bad_code"
        elif not _month_ok(month):
            reason = "bad_month"
        elif not (isinstance(isin, str) and ISIN.match(isin)):
            reason = "bad_isin"
        elif w is None or not -100 <= w <= 100 or w == 0:
            reason = "bad_weight"
        elif (code, month, isin) in seen:
            reason = "duplicate"
        else:
            reason = None
        if reason:
            out.skipped[reason] += 1
            continue
        seen.add((code, month, isin))
        out.rows.append((code, month, isin, w, _num(mv) if mv is not None else None))
    return out


def build_constituents(records) -> Parsed:
    out, seen = Parsed(), set()
    for index_name, month, isin, weight in records:
        w = _num(weight)
        if not (isinstance(index_name, str) and index_name.strip()):
            reason = "bad_index"
        elif not _month_ok(month):
            reason = "bad_month"
        elif not (isinstance(isin, str) and ISIN.match(isin)):
            reason = "bad_isin"
        elif w is None or not 0 < w <= 100:
            reason = "bad_weight"
        elif (index_name, month, isin) in seen:
            reason = "duplicate"
        else:
            reason = None
        if reason:
            out.skipped[reason] += 1
            continue
        seen.add((index_name, month, isin))
        out.rows.append((index_name.strip(), month, isin, w))
    return out


def build_expense_ratios(records) -> Parsed:
    out, seen = Parsed(), set()
    for code, as_of, ter in records:
        t = _num(ter)
        if not (isinstance(code, str) and code.isdigit()) or not isinstance(as_of, date):
            reason = "bad_key"
        elif t is None or not 0 <= t < 10:
            reason = "bad_ter"
        elif (code, as_of) in seen:
            reason = "duplicate"
        else:
            reason = None
        if reason:
            out.skipped[reason] += 1
            continue
        seen.add((code, as_of))
        out.rows.append((code, as_of, t))
    return out


def _upsert(conn: psycopg.Connection, table: str, cols: list[str], key: list[str], parsed: Parsed, source: str,
            fk_scheme: bool) -> dict:
    values = [c for c in cols if c not in key] + ["source"]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE _t (LIKE {table})")
        with cur.copy(f"COPY _t ({', '.join(cols)}, source) FROM STDIN") as cp:
            for r in parsed.rows:
                cp.write_row((*r, source))
        unknown = 0
        if fk_scheme:  # rows for schemes not in fund_schemes would break the foreign key
            cur.execute("DELETE FROM _t t WHERE NOT EXISTS (SELECT 1 FROM fund_schemes s WHERE s.amfi_code = t.amfi_code)")
            unknown = cur.rowcount
        cur.execute(
            f"""INSERT INTO {table} ({', '.join(cols)}, source) SELECT {', '.join(cols)}, source FROM _t
                ON CONFLICT ({', '.join(key)}) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in values)}
                WHERE ({', '.join(f'{table}.{c}' for c in values)}) IS DISTINCT FROM ({', '.join(f'EXCLUDED.{c}' for c in values)})
                RETURNING xmax = 0""")
        written = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute("DROP TABLE _t")
    skipped = dict(parsed.skipped)
    if unknown:
        skipped["unknown_scheme"] = unknown
    return {"inserted": written[True], "updated": written[False], "skipped": skipped}


def load_holdings(conn, parsed: Parsed, source: str = "dev_local") -> dict:
    return _upsert(conn, "fund_holdings", ["amfi_code", "month", "isin", "weight_pct", "market_value"],
                   ["amfi_code", "month", "isin"], parsed, source, fk_scheme=True)


def load_constituents(conn, parsed: Parsed, source: str = "dev_local") -> dict:
    return _upsert(conn, "index_constituents", ["index_name", "month", "isin", "weight_pct"],
                   ["index_name", "month", "isin"], parsed, source, fk_scheme=False)


def load_expense_ratios(conn, parsed: Parsed, source: str = "dev_local") -> dict:
    return _upsert(conn, "fund_expense_ratios", ["amfi_code", "as_of", "ter_pct"], ["amfi_code", "as_of"],
                   parsed, source, fk_scheme=True)


def run_month(conn: psycopg.Connection, year: int, month: int, adapter) -> dict:
    """Load one disclosure month of all three datasets; a dataset the adapter lacks reports no_data."""
    out = {}
    for name, fetch, loader in (("holdings", adapter.fund_holdings, load_holdings),
                                ("constituents", adapter.index_constituents, load_constituents),
                                ("expense_ratios", adapter.expense_ratios, load_expense_ratios)):
        parsed = fetch(year, month)
        out[name] = {"status": "no_data"} if parsed is None else {"status": "loaded", **loader(conn, parsed, adapter.name)}
    return out


if __name__ == "__main__":
    from services.pipelines.sources import ADAPTERS, get_adapter

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--source", choices=sorted(ADAPTERS))
    args = ap.parse_args()
    y, m = map(int, args.month.split("-"))
    with psycopg.connect(DATABASE_URL) as conn:
        print(run_month(conn, y, m, get_adapter(args.source)))
