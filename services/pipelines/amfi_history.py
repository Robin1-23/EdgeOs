"""AMFI NAV history backfill: one month of NAVs per call -> fund_nav (+ missing fund_schemes).

Rows come from a source adapter's fund_navs_month (services/pipelines/sources.py); this module never downloads.
Usage: uv run python -m services.pipelines.amfi_history --from 2016-10 --to 2026-10 [--source dev_local]

Existing schemes are never updated from history: old reports carry old names and categories.
"""

import argparse
import sys
from datetime import date

import psycopg

from db import DATABASE_URL
from services.pipelines import amfi_nav
from services.pipelines.common import PipelineError

MIN_SCHEMES = 2000  # a month report lists ~10-15k schemes; read at call time so tests can lower it


def run_month(conn: psycopg.Connection, year: int, month: int, today: date, adapter) -> dict:
    parsed = adapter.fund_navs_month(year, month, today)
    if parsed is None:
        raise PipelineError(f"{adapter.name}: no NAV history for {year}-{month:02d}")
    return {"month": f"{year}-{month:02d}", "status": "loaded",
            **amfi_nav.load(conn, parsed, min_schemes=MIN_SCHEMES, update_schemes=False, source=adapter.name)}


def months(start: str, end: str) -> list[tuple[int, int]]:
    (y, m), (y2, m2) = (map(int, s.split("-")) for s in (start, end))
    out = []
    while (y, m) <= (y2, m2):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


if __name__ == "__main__":
    from services.pipelines.sources import ADAPTERS, get_adapter

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM")
    ap.add_argument("--source", choices=sorted(ADAPTERS))
    args = ap.parse_args()
    adapter, failed = get_adapter(args.source), False
    with psycopg.connect(DATABASE_URL) as conn:
        for y, m in months(args.start, args.end):
            try:
                result = run_month(conn, y, m, date.today(), adapter)
            except PipelineError as e:
                result, failed = {"month": f"{y}-{m:02d}", "status": "failed", "error": str(e)}, True
            print(result, flush=True)
    sys.exit(1 if failed else 0)
