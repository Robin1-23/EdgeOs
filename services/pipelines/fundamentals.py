"""Company financials: canonical line items + loader -> fundamentals_quarterly. Idempotent.

Every source adapter maps its feed onto ITEMS (amounts in INR crore, EPS in INR). Items a source does not supply
are simply absent; agents report them as gaps. Banks/NBFCs/insurers use different statements and are not covered
by these items yet.
Usage: uv run python -m services.pipelines.fundamentals --isin INE002A01018 [--source licensed_vendor]
       uv run python -m services.pipelines.fundamentals --all
"""

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

import psycopg

from db import DATABASE_URL
from services.pipelines.common import PipelineError

CRORE, RUPEE = "INR cr", "INR"
# item -> (statement, unit, meaning). Sign convention: amounts as reported, expenses/outflows positive.
ITEMS = {
    # P&L (quarterly and annual)
    "revenue_ops": ("pl", CRORE, "revenue from operations"),
    "other_income": ("pl", CRORE, "other income"),
    "depreciation": ("pl", CRORE, "depreciation and amortisation"),
    "finance_cost": ("pl", CRORE, "finance costs"),
    "exceptional_items": ("pl", CRORE, "exceptional items, gain positive / loss negative (0 when none)"),
    "pbt": ("pl", CRORE, "profit before tax, after exceptional items"),
    "tax": ("pl", CRORE, "total tax expense"),
    "pat": ("pl", CRORE, "profit after tax, including non-controlling interests"),
    "pat_owners": ("pl", CRORE, "profit attributable to owners of the parent (= pat for standalone)"),
    "eps_basic": ("pl", RUPEE, "basic EPS"),
    # balance sheet (as at period_end; annual, half-yearly when published)
    "equity_total": ("bs", CRORE, "total equity including non-controlling interests"),
    "equity_owners": ("bs", CRORE, "equity attributable to owners of the parent"),
    "borrowings_current": ("bs", CRORE, "current borrowings incl. current maturities of long-term debt"),
    "borrowings_noncurrent": ("bs", CRORE, "non-current borrowings"),
    "cash_and_equivalents": ("bs", CRORE, "cash, cash equivalents and other bank balances"),
    "total_assets": ("bs", CRORE, "total assets"),
    "shares_outstanding": ("bs", "cr shares", "equity shares outstanding at period end, in crore (not adjusted later)"),
    "contingent_liabilities": ("bs", CRORE, "contingent liabilities not provided for (notes to accounts)"),
    # cash flow (annual, half-yearly when published)
    "cfo": ("cf", CRORE, "net cash from operating activities"),
    "capex": ("cf", CRORE, "purchase of property, plant, equipment and intangibles (outflow, positive)"),
}
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")


@dataclass
class Parsed:
    """Normalised financials, the shape every source adapter returns: rows are
    (isin, period_end, period_type 'Q'|'A', consolidated, item, value) with item in ITEMS, validated, one per key."""
    rows: list[tuple] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)


def build(records) -> Parsed:
    """Validate raw (isin, period_end, period_type, consolidated, item, value) records from an adapter's mapping."""
    out, seen = Parsed(), set()
    for isin, period_end, period_type, consolidated, item, value in records:
        if not isinstance(isin, str) or not ISIN.match(isin):
            reason = "bad_isin"
        elif not isinstance(period_end, date):
            reason = "bad_period_end"
        elif period_type not in ("Q", "A"):
            reason = "bad_period_type"
        elif item not in ITEMS:
            reason = "unknown_item"
        else:
            try:
                v = Decimal(str(value))
                reason = None if v.is_finite() else "bad_value"
            except InvalidOperation:
                reason = "bad_value"
        key = (isin, period_end, period_type, bool(consolidated), item)
        if reason is None and key in seen:
            reason = "duplicate"
        if reason:
            out.skipped[reason] += 1
            continue
        seen.add(key)
        out.rows.append((*key, v))
    return out


def load(conn: psycopg.Connection, parsed: Parsed, source: str = "dev_local") -> dict:
    """Upsert; rows for ISINs not in securities are skipped (prices pipeline creates securities)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _fund (isin text, period_end date, period_type text, consolidated boolean,"
                    " item text, value numeric, unit text)")
        with cur.copy("COPY _fund FROM STDIN") as cp:
            for r in parsed.rows:
                cp.write_row((*r, ITEMS[r[4]][1]))
        (unknown,) = cur.execute("SELECT count(*) FROM _fund f WHERE NOT EXISTS (SELECT 1 FROM securities s WHERE s.isin = f.isin)").fetchone()
        cur.execute(
            """INSERT INTO fundamentals_quarterly (security_id, period_end, period_type, consolidated, item, value, unit, source)
               SELECT s.id, f.period_end, f.period_type, f.consolidated, f.item, f.value, f.unit, %s
               FROM _fund f JOIN securities s USING (isin)
               ON CONFLICT (security_id, period_end, period_type, consolidated, item) DO UPDATE SET
                   value = EXCLUDED.value, unit = EXCLUDED.unit, source = EXCLUDED.source
               WHERE (fundamentals_quarterly.value, fundamentals_quarterly.unit, fundamentals_quarterly.source)
                     IS DISTINCT FROM (EXCLUDED.value, EXCLUDED.unit, EXCLUDED.source)
               RETURNING xmax = 0""",
            (source,),
        )
        written = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute("DROP TABLE _fund")
    skipped = dict(parsed.skipped)
    if unknown:
        skipped["unknown_security"] = unknown
    return {"rows_inserted": written[True], "rows_updated": written[False], "skipped": skipped}


def run_security(conn: psycopg.Connection, isin: str, adapter) -> dict:
    parsed = adapter.fundamentals(isin)
    if parsed is None:
        return {"isin": isin, "status": "no_data"}
    return {"isin": isin, "status": "loaded", **load(conn, parsed, source=adapter.name)}


if __name__ == "__main__":
    from services.pipelines.sources import ADAPTERS, get_adapter

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--isin")
    who.add_argument("--all", action="store_true", help="every security with a current NSE symbol")
    ap.add_argument("--source", choices=sorted(ADAPTERS))
    args = ap.parse_args()
    adapter, failed = get_adapter(args.source), False
    with psycopg.connect(DATABASE_URL) as conn:
        isins = [args.isin] if args.isin else [i for (i,) in conn.execute(
            "SELECT isin FROM securities WHERE nse_symbol IS NOT NULL ORDER BY isin")]
        for isin in isins:
            try:
                result = run_security(conn, isin, adapter)
            except PipelineError as e:
                result, failed = {"isin": isin, "status": "failed", "error": str(e)}, True
            print(result, flush=True)
    sys.exit(1 if failed else 0)
