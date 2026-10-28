"""AMFI-format NAV parser + loader (daily NAVAll.txt or the NAV history report) -> fund_schemes + fund_nav. Idempotent.

Rows come from a source adapter (services/pipelines/sources.py); this module never downloads.
Usage: uv run python -m services.pipelines.amfi_nav [--date YYYY-MM-DD] [--source dev_local]
"""

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import psycopg

from db import DATABASE_URL
from services.pipelines.common import PipelineError

HEADER = "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date"
HISTORY_HEADER = "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date"
# Column positions of (code, isin_growth, isin_reinvest, name, plan, option, nav, date) per format.
COLUMNS = {HEADER: (0, 1, 2, 3, 4, 5, 6, 7), HISTORY_HEADER: (0, 4, 5, 1, 2, 3, 6, 7)}
SECTION = re.compile(r"^(Open Ended|Close Ended|Interval Fund) Schemes\s*\(\s*(.+?)\s*\)$")  # history adds spaces
SCHEME_TYPE = {"Open Ended": "open", "Close Ended": "close", "Interval Fund": "interval"}
PLAN = {"Direct Plan": "direct", "Regular Plan": "regular"}
MIN_SCHEMES = 5000  # full file has ~14k; fewer means a truncated or broken download

SCHEME_COLS = "amfi_code, name, amc, category, scheme_type, plan, option, isin_growth, isin_reinvest"


@dataclass
class Parsed:
    """Normalised NAV data, the shape every source adapter returns. schemes: SCHEME_COLS tuples (plan 'direct' |
    'regular' | None, category as published); navs: (amfi_code, nav_date, nav > 0), validated, one per code and date."""
    schemes: list[tuple] = field(default_factory=list)  # SCHEME_COLS order
    navs: list[tuple] = field(default_factory=list)  # (amfi_code, nav_date, nav)
    skipped: Counter = field(default_factory=Counter)  # reason -> rows


def _parse_row(line: str, today: date, cols: tuple[int, ...]) -> tuple | str:
    """Return (code, isin_growth, isin_reinvest, name, plan, option, nav, nav_date) or a skip reason."""
    f = [x.strip() for x in line.split(";")]
    if len(f) != 8:
        return "field_count"
    code, isin_g, isin_r, name, plan, option, nav_s, date_s = (f[i] for i in cols)
    if not code.isdigit():
        return "bad_code"
    if plan and plan not in PLAN:
        return "unknown_plan"
    try:
        nav = Decimal(nav_s)
    except InvalidOperation:
        return "bad_nav"
    if nav == 0:
        return "zero_nav"  # e.g. written-off segregated portfolios; fund_nav requires nav > 0
    if not nav.is_finite() or nav < 0:
        return "bad_nav"
    try:
        nav_date = datetime.strptime(date_s, "%d-%b-%Y").date()
    except ValueError:
        return "bad_date"
    if nav_date > today:
        return "future_date"
    isin_g, isin_r = (None if s in ("", "-") else s for s in (isin_g, isin_r))
    return code, isin_g, isin_r, name, PLAN.get(plan), option or None, nav, nav_date


def parse(text: str, today: date | None = None) -> Parsed:
    """Daily NAVAll.txt (one NAV per scheme) or the NAV history report (one row per scheme per day)."""
    today = today or date.today()
    lines = text.lstrip("﻿").splitlines()
    cols = COLUMNS.get(lines[0].strip()) if lines else None
    if cols is None:
        raise PipelineError(f"unexpected header: {(lines[0][:120] if lines else '<empty file>')!r}")

    out, seen, seen_navs = Parsed(), set(), set()
    scheme_type = category = amc = None
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        if ";" not in line:
            if m := SECTION.match(line):
                # AMC carries over: the real file sometimes starts a section with no AMC line.
                scheme_type, category = SCHEME_TYPE[m[1]], m[2]
            else:
                amc = line
            continue

        row = _parse_row(line, today, cols)
        if isinstance(row, str):
            out.skipped[row] += 1
            continue
        code, isin_g, isin_r, name, plan, option, nav, nav_date = row
        if category is None or amc is None:
            out.skipped["no_section"] += 1
        elif (code, nav_date) in seen_navs:
            out.skipped["duplicate_nav"] += 1
        else:
            seen_navs.add((code, nav_date))
            out.navs.append((code, nav_date, nav))
            if code not in seen:
                seen.add(code)
                out.schemes.append((code, name, amc, category, scheme_type, plan, option, isin_g, isin_r))
    return out


def load(conn: psycopg.Connection, parsed: Parsed, min_schemes: int | None = None, update_schemes: bool = True,
         source: str = "dev_local") -> dict:
    """Upsert in one transaction; rows are written only when a value actually changed.
    update_schemes=False (history backfill) only inserts missing schemes: old reports carry old names and
    categories that must not overwrite the current master."""
    min_schemes = MIN_SCHEMES if min_schemes is None else min_schemes
    if len(parsed.schemes) < min_schemes:
        raise PipelineError(f"only {len(parsed.schemes)} valid schemes (< {min_schemes}); refusing to load a partial file")

    set_cols = [c.strip() for c in SCHEME_COLS.split(",")][1:] + ["source"]
    on_conflict = (
        f"""DO UPDATE SET {", ".join(f"{c} = EXCLUDED.{c}" for c in set_cols)}
            WHERE ({", ".join(f"fund_schemes.{c}" for c in set_cols)})
                  IS DISTINCT FROM ({", ".join(f"EXCLUDED.{c}" for c in set_cols)})"""
        if update_schemes else "DO NOTHING"
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _schemes (LIKE fund_schemes); CREATE TEMP TABLE _navs (LIKE fund_nav)")
        with cur.copy(f"COPY _schemes ({SCHEME_COLS}, source) FROM STDIN") as cp:
            for r in parsed.schemes:
                cp.write_row((*r, source))
        with cur.copy("COPY _navs (amfi_code, nav_date, nav, source) FROM STDIN") as cp:
            for r in parsed.navs:
                cp.write_row((*r, source))

        cur.execute(
            f"""INSERT INTO fund_schemes ({SCHEME_COLS}, source) SELECT {SCHEME_COLS}, source FROM _schemes
                ON CONFLICT (amfi_code) {on_conflict}
                RETURNING xmax = 0"""
        )
        schemes = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute(
            """INSERT INTO fund_nav (amfi_code, nav_date, nav, source) SELECT amfi_code, nav_date, nav, source FROM _navs
               ON CONFLICT (amfi_code, nav_date) DO UPDATE SET nav = EXCLUDED.nav, source = EXCLUDED.source
               WHERE (fund_nav.nav, fund_nav.source) IS DISTINCT FROM (EXCLUDED.nav, EXCLUDED.source)
               RETURNING xmax = 0"""
        )
        navs = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute("DROP TABLE _schemes, _navs")

    return {
        "schemes_inserted": schemes[True],
        "schemes_updated": schemes[False],
        "navs_inserted": navs[True],
        "navs_updated": navs[False],
        "skipped": dict(parsed.skipped),
    }


def run(conn: psycopg.Connection, adapter, today: date | None = None) -> dict:
    """Load the latest NAVs from a source adapter (see services/pipelines/sources.py)."""
    today = today or date.today()
    parsed = adapter.fund_navs_latest(today)
    if parsed is None:
        return {"status": "no_file"}
    return {"status": "loaded", **load(conn, parsed, source=adapter.name)}


if __name__ == "__main__":
    from services.pipelines.sources import ADAPTERS, get_adapter

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, default=date.today())
    ap.add_argument("--source", choices=sorted(ADAPTERS))
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL) as conn:
        print(run(conn, get_adapter(args.source), args.date))
