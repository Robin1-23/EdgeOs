"""EdgeOS API: read endpoints over the nightly cache (reports, agent outputs, prices, NAVs), the fund overlap tool
and the ask box. Run: uv run uvicorn services.api.main:app --reload

Licence gate (CLAUDE.md rule 8), fails closed: a report, agent output or data row is served only when it is
public_ok (every source behind it is licensed for display). Otherwise the endpoint answers 404 with the reason.
SERVE_DEV_DATA=1 lets a developer see dev_local data on their own machine: allowed only with DATA_SOURCE=dev_local,
every affected response says "dev_only": true, and the API refuses to start with it in any other configuration.
"""

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import DATABASE_URL
from services.agents import formulas, registry
from services.agents.desks.funds import mf1_xray
from services.agents.mother import orchestrator, planner
from services.agents.runlog import is_public
from services.api.auth import current_user

IST = ZoneInfo("Asia/Kolkata")
NOT_LICENSED = "not available under the current data licence"
HOME_INDICES = ["Nifty 50", "Nifty Next 50", "Nifty Bank", "NIFTY Midcap 100", "NIFTY Smallcap 100", "India VIX"]
ASK_DAILY_LIMIT = int(os.environ.get("ASK_DAILY_LIMIT", "20"))
CACHE_PUBLIC = "public, max-age=300"  # reports change once a night


def dev_mode() -> bool:
    return os.environ.get("SERVE_DEV_DATA") == "1"


def check_config() -> None:
    if dev_mode() and os.environ.get("DATA_SOURCE", "dev_local") != "dev_local":
        raise RuntimeError("SERVE_DEV_DATA=1 is allowed only with DATA_SOURCE=dev_local")


@asynccontextmanager
async def lifespan(_app):
    check_config()
    yield


app = FastAPI(title="EdgeOS API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["GET", "POST"],
                   allow_headers=["Authorization", "Content-Type"])


def get_conn():
    # ponytail: one connection per request; add psycopg_pool if load tests ask for it
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]
VISIBLE = "(SELECT public_ok FROM data_sources WHERE name = {}.source)"  # per-row licence check in SQL


def visible_sql(alias: str) -> str:
    """SQL condition: the row's source may be shown (always true in dev mode)."""
    return "true" if dev_mode() else VISIBLE.format(alias)


def gate(public_ok: bool, response: Response | None = None) -> bool:
    """Raise 404 unless the item may be shown; returns dev_only. Dev-only answers are never publicly cached."""
    if not public_ok and not dev_mode():
        raise HTTPException(404, NOT_LICENSED)
    if response is not None:
        response.headers["Cache-Control"] = "private, no-store" if not public_ok else CACHE_PUBLIC
    return not public_ok


def with_formulas(env: dict) -> dict:
    for m in env["metrics"]:
        m["formula"] = formulas.describe(env["agent_id"], m["formula_id"])
    return env


# --- reads ----------------------------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/formulas")
def all_formulas() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for (agent, fid), text in formulas.FORMULAS.items():
        out.setdefault(agent, {})[fid] = text
    return out


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@app.get("/api/search")
def search(conn: Conn, q: Annotated[str, Query(min_length=1, max_length=64)]) -> dict:
    """Up to 10 stocks and 10 open-ended funds by symbol, ISIN, AMFI code or name; exact and prefix matches first."""
    q = q.strip()
    stocks = conn.execute(
        f"""SELECT nse_symbol, name, isin FROM securities s
            WHERE nse_symbol IS NOT NULL AND {visible_sql('s')}
              AND (upper(nse_symbol) LIKE upper(%(p)s) OR name ILIKE %(like)s ESCAPE '\\' OR isin = upper(%(q)s))
            ORDER BY upper(nse_symbol) = upper(%(q)s) DESC, upper(nse_symbol) LIKE upper(%(p)s) DESC, name, nse_symbol
            LIMIT 10""", {"q": q, "p": _like(q)[1:], "like": _like(q)}).fetchall()
    funds = conn.execute(
        f"""SELECT amfi_code, name FROM fund_schemes f
            WHERE scheme_type = 'open' AND {visible_sql('f')} AND (amfi_code = %(q)s OR name ILIKE %(like)s ESCAPE '\\')
            ORDER BY amfi_code = %(q)s DESC, name LIMIT 10""", {"q": q, "like": _like(q)}).fetchall()
    return {"dev_only": dev_mode(),
            "stocks": [{"symbol": s, "name": n, "isin": i} for s, n, i in stocks],
            "funds": [{"amfi_code": c, "name": n} for c, n in funds]}


@app.get("/api/home")
def home(conn: Conn) -> dict:
    """Latest close and day change of the headline indices, and NSE advances / declines on the latest session."""
    indices = conn.execute(
        f"""SELECT name, trade_date, close, change FROM (
                SELECT n.name, l.trade_date, l.close,
                       l.close / lead(l.close) OVER w - 1 AS change, row_number() OVER w AS rn
                FROM unnest(%s::text[]) AS n(name),
                     LATERAL (SELECT i.trade_date, i.close FROM index_prices i WHERE i.index_name = n.name
                                AND {visible_sql('i')} ORDER BY i.trade_date DESC LIMIT 2) l
                WINDOW w AS (PARTITION BY n.name ORDER BY l.trade_date DESC)) x
            WHERE rn = 1""", (HOME_INDICES,)).fetchall()
    order = {n: k for k, n in enumerate(HOME_INDICES)}
    # dates first, as values: a subquery in the join would stop TimescaleDB skipping old chunks (5 s -> ms)
    (day,) = conn.execute("SELECT max(trade_date) FROM prices_daily WHERE exchange = 'NSE'").fetchone()
    (prev,) = conn.execute("SELECT max(trade_date) FROM prices_daily WHERE exchange = 'NSE' AND trade_date < %s",
                           (day,)).fetchone() if day else (None,)
    breadth = conn.execute(
        f"""SELECT %(day)s::date, count(*) FILTER (WHERE a.close > b.close), count(*) FILTER (WHERE a.close < b.close),
                   count(*) FILTER (WHERE a.close = b.close)
            FROM prices_daily a JOIN prices_daily b ON b.security_id = a.security_id AND b.exchange = 'NSE'
            WHERE a.exchange = 'NSE' AND a.trade_date = %(day)s AND b.trade_date = %(prev)s
              AND {visible_sql('a')} AND {visible_sql('b')}""", {"day": day, "prev": prev}).fetchone() if prev else (None,)
    return {
        "dev_only": dev_mode(),
        "indices": [{"name": n, "date": d, "close": float(c), "change": float(ch) if ch is not None else None}
                    for n, d, c, ch in sorted(indices, key=lambda r: order[r[0]])],
        "breadth": None if breadth[0] is None else
        {"date": breadth[0], "advances": breadth[1], "declines": breadth[2], "unchanged": breadth[3],
         "formula": "Stocks whose NSE close rose, fell or was unchanged versus the previous session's close."},
    }


def _page(conn, kind: str, key: str, response: Response) -> dict:
    row = conn.execute("SELECT as_of, public_ok, report FROM reports WHERE subject = %s AND status = 'published'"
                       " ORDER BY as_of DESC LIMIT 1", (f"{kind}:{key}",)).fetchone()
    if row is None:
        raise HTTPException(404, f"no published report for {kind} {key} yet")
    as_of, public_ok, report = row
    dev_only = gate(public_ok, response)
    agents = {}
    for agent_id in registry.PAGE_AGENTS[kind]:
        run_id = planner.cached_run(conn, agent_id, key, as_of, registry.AGENTS[agent_id].freshness_days)
        out = conn.execute("SELECT envelope, public_ok FROM agent_outputs WHERE run_id = %s", (run_id,)).fetchone() if run_id else None
        if out is None:
            agents[agent_id] = {"available": False, "reason": "no result for this date"}
        elif not out[1] and not dev_mode():
            agents[agent_id] = {"available": False, "reason": NOT_LICENSED}
        else:
            agents[agent_id] = {"available": True, "envelope": with_formulas(out[0])}
    return {"dev_only": dev_only, "subject": key, "as_of": as_of, "report": report, "agents": agents}


def _stock_key(conn, symbol: str) -> str:
    if (key := planner.resolve_stock(conn, symbol)) is None:
        raise HTTPException(404, f"no single stock matches {symbol!r}")
    return key


def _fund_key(conn, code: str) -> str:
    if (key := planner.resolve_fund(conn, code)) is None:
        raise HTTPException(404, f"no fund with AMFI code {code!r}")
    return key


@app.get("/api/stocks/{symbol}")
def stock_page(conn: Conn, symbol: str, response: Response) -> dict:
    return _page(conn, "stock", _stock_key(conn, symbol), response)


@app.get("/api/stocks/{symbol}/prices")
def stock_prices(conn: Conn, symbol: str, response: Response, start: Annotated[date | None, Query(alias="from")] = None) -> dict:
    """Daily NSE bars for the chart (unadjusted for splits and bonuses), default the last 2 years."""
    key = _stock_key(conn, symbol)
    rows = conn.execute(
        f"""SELECT p.trade_date, p.open, p.high, p.low, p.close, p.volume, p.source FROM prices_daily p
            JOIN securities s ON s.id = p.security_id
            WHERE (s.nse_symbol = %(k)s OR s.isin = %(k)s) AND p.exchange = 'NSE' AND p.trade_date >= %(start)s
              AND {visible_sql('p')} ORDER BY p.trade_date""",
        {"k": key, "start": start or date.today() - timedelta(days=730)}).fetchall()
    if not rows:
        raise HTTPException(404, f"no price data for {key}" + ("" if dev_mode() else f" {NOT_LICENSED}"))
    dev_only = gate(not dev_mode(), response)  # rows were already filtered by licence
    return {"dev_only": dev_only, "subject": key, "sources": sorted({r[6] for r in rows}),
            "bars": [{"date": d, "open": float(o), "high": float(h), "low": float(lo), "close": float(c), "volume": v}
                     for d, o, h, lo, c, v, _ in rows]}


@app.get("/api/funds/{code}")
def fund_page(conn: Conn, code: str, response: Response, start: Annotated[date | None, Query(alias="from")] = None) -> dict:
    key = _fund_key(conn, code)
    page = _page(conn, "fund", key, response)
    navs = conn.execute(f"SELECT nav_date, nav FROM fund_nav n WHERE amfi_code = %s AND nav_date >= %s AND {visible_sql('n')}"
                        " ORDER BY nav_date", (key, start or date.today() - timedelta(days=5 * 366))).fetchall()
    page["nav"] = [{"date": d, "nav": float(v)} for d, v in navs]
    return page


# --- on demand ------------------------------------------------------------------------------------------

class OverlapRequest(BaseModel):
    funds: list[mf1_xray.FundPosition] = Field(min_length=2, max_length=20)
    as_of: date | None = None


@app.post("/api/overlap")
def overlap(conn: Conn, req: OverlapRequest, response: Response) -> dict:
    """MF1 on the given schemes (code only, no LLM): pairwise overlap, combined exposure, cost gaps."""
    as_of = req.as_of or conn.execute("SELECT max(nav_date) FROM fund_nav").fetchone()[0] or date.today()
    try:
        inp = mf1_xray.MF1Input(funds=req.funds, as_of=as_of)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    env = mf1_xray.run(conn, inp)
    dev_only = gate(is_public(conn, env), response)
    return {"dev_only": dev_only, "envelope": with_formulas(env.model_dump(mode="json"))}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.post("/api/ask")
def ask(conn: Conn, req: AskRequest, uid: Annotated[str, Depends(current_user)]) -> dict:
    """Signed-in users only, ASK_DAILY_LIMIT questions per IST day; answered from verified agent evidence."""
    day_start = datetime.combine(datetime.now(IST).date(), datetime.min.time(), IST)
    (used,) = conn.execute("SELECT count(*) FROM ask_answers WHERE user_id = %s AND created_at >= %s", (uid, day_start)).fetchone()
    if used >= ASK_DAILY_LIMIT:
        raise HTTPException(429, f"daily limit of {ASK_DAILY_LIMIT} questions reached; it resets at midnight IST")

    def connect():
        return psycopg.connect(DATABASE_URL, autocommit=True)

    # ponytail: the user is attached after the answer, so a run that crashes mid-way is not counted against the quota
    out = orchestrator.ask(conn, req.question, datetime.now(IST).date(), connect)
    conn.execute("UPDATE ask_answers SET user_id = %s WHERE id = %s", (uid, out["id"]))
    if "report" not in out:
        return {"status": out["status"], "message": out["message"], "remaining": ASK_DAILY_LIMIT - used - 1}
    report = out["report"]
    if out["status"] == "blocked":
        return {"status": "blocked", "message": report.blocked_reason or "no answer could be shown",
                "remaining": ASK_DAILY_LIMIT - used - 1}
    dev_only = gate(report.public_ok)
    return {"status": out["status"], "dev_only": dev_only, "answer": report.model_dump(mode="json"),
            "remaining": ASK_DAILY_LIMIT - used - 1}
