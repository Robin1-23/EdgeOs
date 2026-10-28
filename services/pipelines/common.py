import argparse
import os
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import psycopg

from db import DATABASE_URL

RAW_DATA_DIR = Path(os.environ.get("RAW_DATA_DIR", "data/raw"))  # files the dev_local adapter reads


class PipelineError(Exception):
    pass


def backfill_days(start: date, end: date, run: Callable[[date], dict]) -> list[dict]:
    """Run each day oldest first; a failed day is reported, not fatal."""
    results = []
    for n in range((end - start).days + 1):
        day = start + timedelta(days=n)
        try:
            results.append(run(day))
        except PipelineError as e:
            results.append({"day": day, "status": "failed", "error": str(e)})
    return results


def day_cli(doc: str, run_day: Callable) -> None:
    """Shared CLI for daily pipelines: --date or --from/--to backfill, from --source (default DATA_SOURCE/dev_local)."""
    from services.pipelines.sources import ADAPTERS, get_adapter  # sources imports the pipeline modules

    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, help="single trading day")
    ap.add_argument("--from", dest="start", type=date.fromisoformat, help="backfill start (inclusive)")
    ap.add_argument("--to", dest="end", type=date.fromisoformat, help="backfill end (inclusive)")
    ap.add_argument("--source", choices=sorted(ADAPTERS), help="data source adapter")
    args = ap.parse_args()
    if bool(args.start) != bool(args.end) or bool(args.date) == bool(args.start):
        ap.error("give --date, or both --from and --to")
    adapter = get_adapter(args.source)

    def run_and_print(day: date) -> dict:
        try:
            result = run_day(conn, day, adapter)
        except PipelineError as e:
            result = {"day": day, "status": "failed", "error": str(e)}
        print(result, flush=True)  # progress for long backfills
        return result

    with psycopg.connect(DATABASE_URL) as conn:
        results = backfill_days(args.date or args.start, args.date or args.end, run_and_print)
    sys.exit(1 if any(r["status"] == "failed" for r in results) else 0)
