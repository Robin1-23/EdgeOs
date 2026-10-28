"""AMFI NAV history backfill: one NAV history report per calendar month -> fund_nav (+ missing fund_schemes).

Usage: uv run python -m services.pipelines.amfi_history --from 2016-10 --to 2026-10

Existing schemes are never updated from history: old reports carry old names and categories.
"""

import argparse
import calendar
import sys
from datetime import date

import psycopg

from db import DATABASE_URL
from services.pipelines import amfi_nav
from services.pipelines.common import RAW_DATA_DIR, PipelineError, cached_get

RAW_DIR = RAW_DATA_DIR / "amfi_history"
URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt={start}&todt={end}"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()  # explicit: %b depends on locale
MIN_SCHEMES = 2000  # a month report lists ~10-15k schemes; read at call time so tests can lower it


def amfi_date(d: date) -> str:
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


def month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def fetch(year: int, month: int, today: date) -> str:
    start, end = month_range(year, month)
    data = cached_get(URL.format(start=amfi_date(start), end=amfi_date(end)), RAW_DIR, name=f"NAVHistory_{start:%Y-%m}.txt", cache=end < today)
    if data is None:
        raise PipelineError(f"no AMFI history report for {start:%Y-%m}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PipelineError(f"{start:%Y-%m}: not UTF-8: {e}") from e


def run_month(conn: psycopg.Connection, year: int, month: int, today: date, fetcher=fetch) -> dict:
    text = fetcher(year, month, today)
    return {"month": f"{year}-{month:02d}", "status": "loaded",
            **amfi_nav.load(conn, amfi_nav.parse(text, today), min_schemes=MIN_SCHEMES, update_schemes=False)}


def months(start: str, end: str) -> list[tuple[int, int]]:
    (y, m), (y2, m2) = (map(int, s.split("-")) for s in (start, end))
    out = []
    while (y, m) <= (y2, m2):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM")
    args = ap.parse_args()
    failed = False
    with psycopg.connect(DATABASE_URL) as conn:
        for y, m in months(args.start, args.end):
            try:
                result = run_month(conn, y, m, date.today())
            except PipelineError as e:
                result, failed = {"month": f"{y}-{m:02d}", "status": "failed", "error": str(e)}, True
            print(result, flush=True)
    sys.exit(1 if failed else 0)
