"""Nightly report batch: mother-agent page reports for every covered stock and fund -> reports (pages read the cache).

Usage: uv run python -m services.agents.batch [--as-of YYYY-MM-DD] [--limit N] [--budget USD] [--workers N]
Normally run by services/pipelines/nightly.py right after the data pipelines.

Coverage (computed in SQL): the top STOCK_COVERAGE NSE stocks by average traded value over the last LIQUIDITY_SESSIONS
sessions, then every open-ended scheme with a NAV on the latest NAV date. Stocks go first, most liquid first.
A subject already published for its date is skipped, so a crashed or capped night resumes where it stopped.
Before each new subject starts, the batch checks the spending cap (LLM cost since the batch began) and the dispatch
deadline; whatever did not start is listed in the summary.
"""

import argparse
import json
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

import psycopg

from db import DATABASE_URL

IST = ZoneInfo("Asia/Kolkata")
STOCK_COVERAGE = 2000
LIQUIDITY_SESSIONS = 20
DEADLINE_IST = dtime(6, 30)  # stop starting new subjects; the spec wants the batch done before 07:00 IST
DEFAULT_BUDGET_USD = float(os.environ.get("NIGHTLY_BUDGET_USD", "150"))
DEFAULT_WORKERS = int(os.environ.get("NIGHTLY_WORKERS", "16"))


def stock_as_of(conn: psycopg.Connection) -> date | None:
    return conn.execute("SELECT max(trade_date) FROM prices_daily WHERE exchange = 'NSE'").fetchone()[0]


def fund_as_of(conn: psycopg.Connection) -> date | None:
    return conn.execute("SELECT max(nav_date) FROM fund_nav").fetchone()[0]


def stock_universe(conn: psycopg.Connection, as_of: date, n: int = STOCK_COVERAGE) -> list[str]:
    """Symbols by average traded value over the last LIQUIDITY_SESSIONS NSE sessions up to as_of. A day without
    a trade counts as zero, so thinly traded stocks rank low. Traded value = turnover, else close x volume."""
    rows = conn.execute(
        """WITH sessions AS (SELECT DISTINCT trade_date FROM prices_daily
                             WHERE exchange = 'NSE' AND trade_date <= %(as_of)s ORDER BY trade_date DESC LIMIT %(k)s)
           SELECT s.nse_symbol FROM prices_daily p JOIN securities s ON s.id = p.security_id
           WHERE p.exchange = 'NSE' AND p.trade_date IN (SELECT trade_date FROM sessions) AND s.nse_symbol IS NOT NULL
           GROUP BY s.nse_symbol
           ORDER BY sum(coalesce(p.turnover, p.close * p.volume)) DESC, s.nse_symbol
           LIMIT %(n)s""", {"as_of": as_of, "k": LIQUIDITY_SESSIONS, "n": n}).fetchall()
    return [r[0] for r in rows]


def fund_universe(conn: psycopg.Connection, as_of: date) -> list[str]:
    rows = conn.execute(
        """SELECT s.amfi_code FROM fund_schemes s JOIN fund_nav n ON n.amfi_code = s.amfi_code AND n.nav_date = %s
           WHERE s.scheme_type = 'open' ORDER BY s.amfi_code""", (as_of,)).fetchall()
    return [r[0] for r in rows]


def default_deadline(now: datetime | None = None) -> datetime:
    """The next 06:30 IST."""
    now = (now or datetime.now(IST)).astimezone(IST)
    d = datetime.combine(now.date(), DEADLINE_IST, IST)
    return d if now < d else d + timedelta(days=1)


def _default_connect():
    return psycopg.connect(DATABASE_URL, autocommit=True)


def run(conn: psycopg.Connection, *, budget_usd: float = DEFAULT_BUDGET_USD, deadline: datetime | None = None,
        workers: int = DEFAULT_WORKERS, connect: Callable | None = None, limit: int | None = None,
        stocks_as_of: date | None = None, funds_as_of: date | None = None, report_fn: Callable | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(IST)) -> dict:
    """Build every due report; return the summary that nightly stores in pipeline_runs.
    `connect` makes one autocommit connection per worker (pass a nullcontext over a test connection with workers=1)."""
    if report_fn is None:
        from services.agents.mother.orchestrator import page_report as report_fn
    connect = connect or _default_connect
    deadline = deadline or default_deadline(now())
    started_at = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    s_as_of, f_as_of = stocks_as_of or stock_as_of(conn), funds_as_of or fund_as_of(conn)
    jobs = [("stock", k, s_as_of) for k in (stock_universe(conn, s_as_of) if s_as_of else [])]
    jobs += [("fund", k, f_as_of) for k in (fund_universe(conn, f_as_of) if f_as_of else [])]
    jobs = jobs[:limit] if limit is not None else jobs
    done = {(subj, d) for subj, d in conn.execute(
        "SELECT subject, as_of FROM reports WHERE status = 'published' AND as_of = ANY(%s)",
        ([d for d in (s_as_of, f_as_of) if d],)).fetchall()}
    lock, stop = threading.Lock(), {"reason": None}

    def spent(c) -> float:
        # ponytail: counts every LLM call since the batch began, ask-box calls included (errs on the safe side)
        return float(c.execute("SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE created_at >= %s",
                               (started_at,)).fetchone()[0])

    def one(job) -> tuple[str, str]:
        kind, key, as_of = job
        if (f"{kind}:{key}", as_of) in done:
            return job, "already_published"
        with connect() as c:
            with lock:
                if stop["reason"] is None:
                    if now() >= deadline:
                        stop["reason"] = "deadline"
                    elif spent(c) >= budget_usd:
                        stop["reason"] = "budget"
                if stop["reason"]:
                    return job, "not_run"
            try:
                return job, report_fn(c, kind, key, as_of, connect=connect).status  # published | blocked
            except Exception as e:  # one subject failing never stops the batch; page_report logged it
                return job, f"failed: {type(e).__name__}: {e}"[:300]

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, jobs))
    counts: dict[str, int] = {}
    for _, status in results:
        counts[status if not status.startswith("failed") else "failed"] = counts.get(status, 0) + 1
    return {
        "status": "partial" if stop["reason"] else "done",
        "stop_reason": stop["reason"],
        "stocks_as_of": s_as_of, "funds_as_of": f_as_of,
        "subjects": {"stock": [k for kind, k, _ in jobs if kind == "stock"], "fund": [k for kind, k, _ in jobs if kind == "fund"]},
        "counts": counts,
        "failed": {f"{j[0]}:{j[1]}": s[len("failed: "):] for j, s in results if s.startswith("failed")},
        "not_run": [f"{j[0]}:{j[1]}" for j, s in results if s == "not_run"],
        "cost_usd": round(spent(conn), 4),
        "seconds": round(time.monotonic() - t0, 1),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", type=date.fromisoformat, help="report date for stocks and funds (default: latest data)")
    ap.add_argument("--limit", type=int, help="only the first N subjects (stocks first)")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD, help="USD cap on LLM spend")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = ap.parse_args()
    with _default_connect() as conn:
        summary = run(conn, budget_usd=args.budget, workers=args.workers, limit=args.limit,
                      stocks_as_of=args.as_of, funds_as_of=args.as_of)
    summary["subjects"] = {k: len(v) for k, v in summary["subjects"].items()}
    print(json.dumps(summary, default=str, indent=2))
