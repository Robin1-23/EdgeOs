"""EdgeOS API: read endpoints over the nightly cache (reports, agent outputs, prices, NAVs), the fund overlap tool
and the ask box. Run: uv run uvicorn services.api.main:app --reload

Licence gate (CLAUDE.md rule 8), fails closed: a report, agent output or data row is served only when it is
public_ok (every source behind it is licensed for display). Otherwise the endpoint answers 404 with the reason.
SERVE_DEV_DATA=1 lets a developer see dev_local data on their own machine: allowed only with DATA_SOURCE=dev_local,
every affected response says "dev_only": true, and the API refuses to start with it in any other configuration.
"""

import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import DATABASE_URL
from services.agents import formulas, registry
from services.agents.desks.funds import mf1_xray
from services.agents.desks.funds.categories import BENCHMARK, sebi_category
from services.agents.mother import orchestrator, planner
from services.agents.runlog import is_public
from services.api import billing
from services.api.auth import current_user

IST = ZoneInfo("Asia/Kolkata")
NOT_LICENSED = "not available under the current data licence"
HOME_INDICES = ["Nifty 50", "Nifty Next 50", "Nifty Bank", "NIFTY Midcap 100", "NIFTY Smallcap 100", "India VIX"]
FREE_LIMIT = int(os.environ.get("ASK_FREE_LIMIT", "5"))  # ask-box questions per IST day
PRO_LIMIT = int(os.environ.get("ASK_PRO_LIMIT", "50"))
HISTORY_DAYS = {"free": 30, "pro": 365}
CACHE_PUBLIC = "public, max-age=300"  # reports change once a night


def dev_mode() -> bool:
    return os.environ.get("SERVE_DEV_DATA") == "1"


def check_config() -> None:
    if dev_mode() and os.environ.get("DATA_SOURCE", "dev_local") != "dev_local":
        raise RuntimeError("SERVE_DEV_DATA=1 is allowed only with DATA_SOURCE=dev_local")


@asynccontextmanager
async def lifespan(_app):
    check_config()
    billing.config()  # refuses live Razorpay keys before the legal review
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


META_SQL = {
    "stock": "SELECT name, isin, nse_symbol, sector, industry FROM securities m WHERE (nse_symbol = %(k)s OR isin = %(k)s) AND {}",
    "fund": "SELECT name, amc, category, plan, option FROM fund_schemes m WHERE amfi_code = %(k)s AND {}",
}
META_COLS = {"stock": ("name", "isin", "symbol", "sector", "industry"), "fund": ("name", "amc", "category", "plan", "option")}


def _agent_slot(conn, agent_id: str, subject: str, as_of: date) -> dict:
    """The agent output a report used (latest ok run within freshness), licence-gated, metrics with formulas."""
    run_id = planner.cached_run(conn, agent_id, subject, as_of, registry.AGENTS[agent_id].freshness_days)
    out = conn.execute("SELECT envelope, public_ok FROM agent_outputs WHERE run_id = %s", (run_id,)).fetchone() if run_id else None
    if out is None:
        return {"available": False, "reason": "no result for this date"}
    if not out[1] and not dev_mode():
        return {"available": False, "reason": NOT_LICENSED}
    return {"available": True, "envelope": with_formulas(out[0])}


def _page(conn, kind: str, key: str, response: Response) -> dict:
    row = conn.execute("SELECT as_of, public_ok, report FROM reports WHERE subject = %s AND status = 'published'"
                       " ORDER BY as_of DESC LIMIT 1", (f"{kind}:{key}",)).fetchone()
    if row is None:
        raise HTTPException(404, f"no published report for {kind} {key} yet")
    as_of, public_ok, report = row
    dev_only = gate(public_ok, response)
    agents = {agent_id: _agent_slot(conn, agent_id, key, as_of) for agent_id in registry.PAGE_AGENTS[kind]}
    meta = conn.execute(META_SQL[kind].format(visible_sql("m")), {"k": key}).fetchone()
    return {"dev_only": dev_only, "subject": key, "as_of": as_of, "report": report, "agents": agents,
            "meta": dict(zip(META_COLS[kind], meta)) if meta else None}


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
def fund_page(conn: Conn, code: str, response: Response) -> dict:
    return _page(conn, "fund", _fund_key(conn, code), response)


def rebase(points: list[tuple[date, float]], base: float) -> list[dict]:
    """Each value as an index of `base` = 100 (100 x value / base)."""
    return [{"date": d, "value": 100 * v / base} for d, v in points]


@app.get("/api/funds/{code}/series")
def fund_series(conn: Conn, code: str, response: Response, start: Annotated[date, Query(alias="from")],
                end: Annotated[date | None, Query(alias="to")] = None) -> dict:
    """The fund's NAV and its category benchmark from `from` to `to`, both rebased to 100 on the fund's first NAV date
    in the range (the benchmark at its latest close on or before that date). The benchmark is a price index, not TRI."""
    key = _fund_key(conn, code)
    end = end or date.today()
    navs = [(d, float(v)) for d, v in conn.execute(
        f"SELECT nav_date, nav FROM fund_nav n WHERE amfi_code = %s AND nav_date BETWEEN %s AND %s AND {visible_sql('n')}"
        " ORDER BY nav_date", (key, start, end))]
    if not navs:
        raise HTTPException(404, f"no NAVs for {key} in this range" + ("" if dev_mode() else f" {NOT_LICENSED}"))
    dev_only = gate(not dev_mode(), response)  # rows were already filtered by licence
    (raw_category,) = conn.execute("SELECT category FROM fund_schemes WHERE amfi_code = %s", (key,)).fetchone()
    bench_name, bench = BENCHMARK.get(sebi_category(raw_category) or ""), None
    if bench_name:
        first = navs[0][0]
        base = conn.execute(f"SELECT close FROM index_prices i WHERE index_name = %s AND trade_date <= %s AND {visible_sql('i')}"
                            " ORDER BY trade_date DESC LIMIT 1", (bench_name, first)).fetchone()
        closes = [(d, float(c)) for d, c in conn.execute(
            f"SELECT trade_date, close FROM index_prices i WHERE index_name = %s AND trade_date BETWEEN %s AND %s"
            f" AND {visible_sql('i')} ORDER BY trade_date", (bench_name, first, end))]
        if base and closes:
            bench = {"name": bench_name, "label": f"{bench_name} (price index, not total return)",
                     "points": rebase(closes, float(base[0]))}
    return {"dev_only": dev_only, "subject": key, "fund": rebase(navs, navs[0][1]), "benchmark": bench,
            "formula": "Each line is 100 x value / value on the first fund NAV date in the range."}


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


def _attempts_today(conn, uid: str) -> int:
    day_start = datetime.combine(datetime.now(IST).date(), datetime.min.time(), IST)
    return conn.execute("SELECT count(*) FROM ask_attempts WHERE user_id = %s AND created_at >= %s", (uid, day_start)).fetchone()[0]


def _plan(conn, uid: str) -> tuple[str, int]:
    return ("pro", PRO_LIMIT) if billing.is_pro(conn, uid) else ("free", FREE_LIMIT)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@app.get("/api/ask/quota")
def ask_quota(conn: Conn, uid: Annotated[str, Depends(current_user)], response: Response) -> dict:
    _no_store(response)
    plan, limit = _plan(conn, uid)
    return {"plan": plan, "limit": limit, "remaining": max(0, limit - _attempts_today(conn, uid))}


def _answer_body(conn, status: str, report: dict, public_ok: bool, as_of: date) -> dict:
    """An answered question with its licence-gated evidence; citation ids carry "SUBJECT/AGENT" when several subjects."""
    dev_only = gate(public_ok)
    pairs = [k.split(" ", 1) for k in report.get("agents", {})]  # "M2 ACME" -> agent, subject
    many = len({s for _, s in pairs}) > 1
    evidence = [{"agent_id": a, "subject": s, "prefix": f"{s}/{a}" if many else a, **_agent_slot(conn, a, s, as_of)}
                for a, s in pairs]
    return {"status": status, "dev_only": dev_only, "answer": report, "evidence": evidence}


@app.post("/api/ask")
def ask(conn: Conn, req: AskRequest, uid: Annotated[str, Depends(current_user)], response: Response) -> dict:
    """Signed-in users only, a daily allowance of attempts by plan; answered from verified agent evidence, which is
    returned with the answer (licence-gated) so every citation can show its number, formula and source."""
    _no_store(response)
    plan, limit = _plan(conn, uid)
    used = _attempts_today(conn, uid)
    if used >= limit:
        raise HTTPException(429, f"daily limit of {limit} questions on the {plan} plan reached; it resets at midnight IST")
    conn.execute("INSERT INTO ask_attempts (user_id) VALUES (%s)", (uid,))  # counted before the run: a crash still counts
    remaining = limit - used - 1

    def connect():
        return psycopg.connect(DATABASE_URL, autocommit=True)

    as_of = datetime.now(IST).date()
    out = orchestrator.ask(conn, req.question, as_of, connect)
    conn.execute("UPDATE ask_answers SET user_id = %s WHERE id = %s", (uid, out["id"]))
    if "report" not in out:
        if out["message"].startswith(planner.PLAN_FAILED):  # our failure, not the question's: keep internals out
            return {"status": "failed", "message": "The research service could not work on this question right now. "
                                                   "Please try again later.", "remaining": remaining}
        return {"status": out["status"], "message": out["message"], "remaining": remaining}
    report = out["report"]
    if out["status"] == "blocked":
        return {"status": "blocked", "message": report.blocked_reason or "no answer could be shown", "remaining": remaining}
    return {**_answer_body(conn, out["status"], report.model_dump(mode="json"), report.public_ok, as_of), "remaining": remaining}


@app.get("/api/ask/{answer_id}")
def ask_again(conn: Conn, answer_id: int, uid: Annotated[str, Depends(current_user)], response: Response) -> dict:
    """Re-open one of the signed-in user's own past answers (anyone else's is 404)."""
    _no_store(response)
    row = conn.execute("SELECT question, as_of, status, answer, public_ok FROM ask_answers WHERE id = %s AND user_id = %s",
                       (answer_id, uid)).fetchone()
    if row is None:
        raise HTTPException(404, "no such question in your history")
    question, as_of, status, answer, public_ok = row
    answer = answer or {}
    if status != "answered":
        message = answer.get("message") or answer.get("blocked_reason") or "no answer could be shown"
        if message.startswith(planner.PLAN_FAILED):
            status, message = "failed", "The research service could not work on this question at the time."
        return {"question": question, "status": status, "message": message}
    return {"question": question, **_answer_body(conn, status, answer, public_ok, as_of)}


# --- account and billing --------------------------------------------------------------------------------

@app.get("/api/account")
def account(conn: Conn, uid: Annotated[str, Depends(current_user)], response: Response) -> dict:
    """Plan, today's allowance, the subscription and the user's question history (30 days free, 12 months Pro)."""
    _no_store(response)
    plan, limit = _plan(conn, uid)
    used = _attempts_today(conn, uid)
    history = conn.execute(
        "SELECT id, question, status, created_at, coalesce(answer->>'message', '') LIKE %s FROM ask_answers WHERE user_id = %s"
        " AND created_at >= now() - make_interval(days => %s) ORDER BY created_at DESC LIMIT 200",
        (planner.PLAN_FAILED + "%", uid, HISTORY_DAYS[plan])).fetchall()
    try:
        billing_on = billing.config() is not None
    except billing.BillingError:
        billing_on = False
    return {"plan": plan, "limit": limit, "used_today": used, "remaining": max(0, limit - used),
            "history_days": HISTORY_DAYS[plan], "subscription": billing.current(conn, uid), "billing_available": billing_on,
            "history": [{"id": i, "question": q, "status": "failed" if failed else st, "asked_at": at}
                        for i, q, st, at, failed in history]}


PAYMENT_UNAVAILABLE = "the payment service could not complete this right now; nothing was charged. Please try again later."


def _billing_config() -> dict:
    try:
        cfg = billing.config()
    except billing.BillingError as e:
        raise HTTPException(503, str(e)) from e
    if cfg is None:
        raise HTTPException(503, "billing is not configured")
    return cfg


@app.post("/api/billing/subscribe")
def subscribe(conn: Conn, uid: Annotated[str, Depends(current_user)], response: Response) -> dict:
    """Start a Pro subscription: returns what Razorpay's checkout needs to collect the mandate in the browser."""
    _no_store(response)
    cfg = _billing_config()
    if billing.is_pro(conn, uid):
        raise HTTPException(409, "you are already on Pro")
    try:
        return billing.subscribe(conn, cfg, uid)
    except billing.BillingError as e:  # details stay in the server log, not on the page
        raise HTTPException(502, PAYMENT_UNAVAILABLE) from e


@app.post("/api/billing/cancel")
def cancel_subscription(conn: Conn, uid: Annotated[str, Depends(current_user)]) -> dict:
    cfg = _billing_config()
    try:
        billing.cancel(conn, cfg, uid)
    except billing.BillingError as e:
        if "no subscription" in str(e):
            raise HTTPException(409, str(e)) from e
        raise HTTPException(502, PAYMENT_UNAVAILABLE) from e
    return {"subscription": billing.current(conn, uid)}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request, conn: Conn, x_razorpay_signature: Annotated[str | None, Header()] = None,
                          x_razorpay_event_id: Annotated[str | None, Header()] = None) -> dict:
    """Razorpay subscription events. Only signed deliveries are applied; repeats and stale events are ignored."""
    cfg = _billing_config()
    body = await request.body()
    if not billing.verify_signature(body, x_razorpay_signature or "", cfg["webhook_secret"]):
        raise HTTPException(400, "bad signature")
    return {"result": billing.apply_webhook(conn, x_razorpay_event_id or "", json.loads(body))}


@app.delete("/api/account")
def delete_account(conn: Conn, uid: Annotated[str, Depends(current_user)]) -> dict:
    """Delete the user's questions and answers. A running Pro subscription must be cancelled first. Subscription and
    payment records are kept for tax and accounting (a legal obligation); the browser then deletes the Firebase user."""
    sub = billing.current(conn, uid)
    if billing.is_pro(conn, uid) and not (sub or {}).get("cancel_at_period_end"):
        raise HTTPException(409, "cancel your Pro subscription before deleting your account")
    answers = conn.execute("DELETE FROM ask_answers WHERE user_id = %s", (uid,)).rowcount
    attempts = conn.execute("DELETE FROM ask_attempts WHERE user_id = %s", (uid,)).rowcount
    return {"deleted": {"answers": answers, "attempts": attempts}}
