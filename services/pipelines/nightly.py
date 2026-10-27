"""Nightly data run: AMFI NAV, then NSE bhavcopy catch-up for the last 7 days. Every run is logged to pipeline_runs.

Usage: uv run python -m services.pipelines.nightly [--date YYYY-MM-DD]   # --date = "today" in IST

Schedule at 00:30 IST, after NSE (~18:00) and AMFI (~23:00) publish. Cron on a UTC server:
  0 19 * * *  cd /app && uv run python -m services.pipelines.nightly
Exit codes: 0 ok, 1 a pipeline failed, 2 another nightly run holds the lock.
"""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from db import DATABASE_URL
from services.pipelines import amfi_nav, nse_bhavcopy

IST = ZoneInfo("Asia/Kolkata")
CATCHUP_DAYS = 7
LOCK_KEY = 7001  # Postgres advisory lock id held for the whole nightly run


def today_ist(now: datetime | None = None) -> date:
    return (now or datetime.now(IST)).astimezone(IST).date()


def nse_days_to_load(conn: psycopg.Connection, today: date) -> list[date]:
    """Weekdays in the window with no successful load yet (late files, missed nights, holidays retried)."""
    window = [today - timedelta(days=n) for n in range(CATCHUP_DAYS - 1, -1, -1)]
    loaded = {
        d for (d,) in conn.execute(
            "SELECT target_date FROM pipeline_runs WHERE pipeline = 'nse_bhavcopy' AND status = 'loaded' AND target_date >= %s",
            (window[0],),
        )
    }
    return [d for d in window if d.weekday() < 5 and d not in loaded]


def _run_logged(conn: psycopg.Connection, pipeline: str, target: date, fn: Callable[[], dict]) -> dict:
    (run_id,) = conn.execute(
        "INSERT INTO pipeline_runs (pipeline, target_date, status) VALUES (%s, %s, 'running') RETURNING id", (pipeline, target)
    ).fetchone()
    try:
        summary = fn()
        summary.pop("day", None)
        status, error = summary.pop("status", "loaded"), None
    except Exception as e:  # scheduler boundary: record it and move on to the next pipeline
        status, summary, error = "failed", None, f"{type(e).__name__}: {e}"
    conn.execute(
        """UPDATE pipeline_runs SET status = %s, summary = %s, error = %s, finished_at = clock_timestamp(),
                  duration_ms = (extract(epoch FROM clock_timestamp() - started_at) * 1000)::int
           WHERE id = %s""",
        (status, Jsonb(summary, dumps=lambda o: json.dumps(o, default=str)) if summary else None, error, run_id),
    )
    return {"pipeline": pipeline, "target_date": target, "status": status, "error": error}


def nightly(conn: psycopg.Connection, today: date) -> list[dict] | None:
    """Run both pipelines; None if another nightly run is in progress. Use an autocommit connection so
    log rows commit on their own and a pipeline rollback never erases its log."""
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
        return None
    try:
        results = [_run_logged(conn, "amfi_nav", today, lambda: amfi_nav.run(conn, today=today))]
        for day in nse_days_to_load(conn, today):
            results.append(_run_logged(conn, "nse_bhavcopy", day, lambda d=day: nse_bhavcopy.run_day(conn, d, nse_bhavcopy.fetch)))
        return results
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def exit_code(results: list[dict] | None) -> int:
    if results is None:
        return 2
    return 1 if any(r["status"] == "failed" for r in results) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, help="treat this as today (IST)")
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        results = nightly(conn, args.date or today_ist())
    for r in results or [{"status": "skipped", "error": "another nightly run holds the lock"}]:
        print(r)
    sys.exit(exit_code(results))
