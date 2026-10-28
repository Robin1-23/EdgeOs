import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import psycopg

from db import DATABASE_URL

RAW_DATA_DIR = Path(os.environ.get("RAW_DATA_DIR", "data/raw"))  # local cache of downloaded source files
POLITE_PAUSE = 1.0  # seconds after each real download
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"


class PipelineError(Exception):
    pass


def http_get(url: str, attempts: int = 3) -> bytes | None:
    """GET with retries. Returns None on 404 (holiday / not published); raises PipelineError otherwise."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except OSError as e:  # URLError, timeouts, resets
            err = e
        if i < attempts - 1:
            time.sleep(5 * 2**i)
    raise PipelineError(f"download failed after {attempts} attempts: {url}: {err}")



def cached_get(url: str, cache_dir: Path, name: str | None = None, cache: bool = True) -> bytes | None:
    """http_get through a local file cache, so reruns and backfills never download twice.
    name overrides the URL basename; cache=False skips writing (e.g. a period still being published)."""
    cached = cache_dir / (name or url.rsplit("/", 1)[1])
    if cached.exists():
        return cached.read_bytes()
    data = http_get(url)
    time.sleep(POLITE_PAUSE)
    if data is not None and cache:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return data


DayRunner = Callable[[psycopg.Connection, date, Callable[[date], bytes | None]], dict]


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


def day_cli(doc: str, run_day: DayRunner, fetch: Callable[[date], bytes | None]) -> None:
    """Shared CLI for daily-file pipelines: --date [--file PATH] or --from/--to backfill."""
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, help="single trading day")
    ap.add_argument("--from", dest="start", type=date.fromisoformat, help="backfill start (inclusive)")
    ap.add_argument("--to", dest="end", type=date.fromisoformat, help="backfill end (inclusive)")
    ap.add_argument("--file", type=Path, help="load a saved file for --date instead of downloading")
    args = ap.parse_args()
    if bool(args.start) != bool(args.end) or bool(args.date) == bool(args.start):
        ap.error("give --date, or both --from and --to")
    if args.file and not args.date:
        ap.error("--file needs --date")

    fetcher = (lambda _: args.file.read_bytes()) if args.file else fetch

    def run_and_print(day: date) -> dict:
        try:
            result = run_day(conn, day, fetcher)
        except PipelineError as e:
            result = {"day": day, "status": "failed", "error": str(e)}
        print(result, flush=True)  # progress for long backfills
        return result

    with psycopg.connect(DATABASE_URL) as conn:
        results = backfill_days(args.date or args.start, args.date or args.end, run_and_print)
    sys.exit(1 if any(r["status"] == "failed" for r in results) else 0)
