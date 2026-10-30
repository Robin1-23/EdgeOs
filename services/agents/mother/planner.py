"""Mother agent, role 1: plan and dispatch.

Page plans are fixed (every specialist for the subject's kind). Ask-box plans use the strong model only to
classify the question and suggest subjects/agents; code resolves subjects against the database (the model can't
invent a security or scheme) and drops agents that don't fit. Fresh cached outputs are reused; the rest run in
dependency waves, in parallel, each with a time budget. Missing or timed-out agents are marked, never guessed.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import psycopg
from pydantic import BaseModel, Field

from services.agents import registry
from services.agents.envelope import Envelope
from services.agents.llm.client import LLMError, call_json
from services.agents.runlog import is_public

MAX_SUBJECTS = 5
PLAN_FAILED = "could not plan the question"  # the planner LLM call failed; callers tell it apart by this prefix
AgentIdLit = Literal["M1", "M2", "M3", "M4", "M5", "M6", "M7", "MF1", "MF2", "MF3"]
UNSUPPORTED_MESSAGE = ("This question is outside what EdgeOS covers today: research on NSE-listed stocks and Indian "
                       "mutual fund schemes. Personal portfolios, F&O and family finances are not covered yet.")
_SUFFIX = re.compile(r"[\s,]+(?:limited|ltd\.?)$", re.IGNORECASE)


@dataclass(frozen=True)
class Task:
    agent_id: str
    subject: str  # the agent's own subject key: NSE symbol / ISIN, AMFI code, or codes joined by '+' (MF1)
    deadline_s: int
    depends_on: tuple[str, ...]
    cached_run_id: int | None = None


@dataclass
class Plan:
    status: Literal["ready", "needs_clarification", "unsupported"]
    kind: str | None = None  # 'stock' | 'fund'
    subjects: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    message: str | None = None
    intent: str | None = None


@dataclass
class Outcome:
    task: Task
    status: Literal["cached", "ok", "failed", "timed_out", "skipped"]
    envelope: Envelope | None = None
    public_ok: bool = False
    run_as_of: date | None = None
    error: str | None = None
    run_id: int | None = None  # the agent_runs row behind the envelope


# --- subjects -------------------------------------------------------------------------------------------

def resolve_stock(conn: psycopg.Connection, text: str) -> str | None:
    """NSE symbol, ISIN, or exact company name (Ltd/Limited ignored) -> subject key (symbol, else ISIN)."""
    t = text.strip()
    rows = conn.execute("SELECT isin, nse_symbol FROM securities WHERE upper(nse_symbol) = upper(%s) OR isin = upper(%s)", (t, t)).fetchall()
    if not rows:
        core = _SUFFIX.sub("", t).lower()
        rows = [(i, s) for i, s, n in conn.execute("SELECT isin, nse_symbol, name FROM securities WHERE name IS NOT NULL")
                if _SUFFIX.sub("", n).lower() == core]
    return (rows[0][1] or rows[0][0]) if len(rows) == 1 else None


def resolve_fund(conn: psycopg.Connection, text: str) -> str | None:
    """AMFI code or exact scheme name (one match only) -> AMFI code."""
    t = text.strip()
    if t.isdigit():
        row = conn.execute("SELECT amfi_code FROM fund_schemes WHERE amfi_code = %s", (t,)).fetchone()
        return row[0] if row else None
    rows = conn.execute("SELECT amfi_code FROM fund_schemes WHERE lower(name) = lower(%s)", (t,)).fetchall()
    return rows[0][0] if len(rows) == 1 else None


# --- plans ----------------------------------------------------------------------------------------------

def cached_run(conn: psycopg.Connection, agent_id: str, subject: str, as_of: date, freshness_days: int) -> int | None:
    """Latest ok run of agent_id for subject whose requested as_of (IST date) is within freshness_days of as_of."""
    row = conn.execute(
        """SELECT r.id FROM agent_runs r JOIN agent_outputs o ON o.run_id = r.id
           WHERE r.agent_id = %s AND r.subject = %s AND r.status = 'ok'
             AND (r.as_of AT TIME ZONE 'Asia/Kolkata')::date BETWEEN %s::date - %s AND %s
           ORDER BY r.as_of DESC, r.id DESC LIMIT 1""", (agent_id, subject, as_of, freshness_days, as_of)).fetchone()
    return row[0] if row else None


def with_dependencies(agent_ids: list[str]) -> list[str]:
    out = list(dict.fromkeys(agent_ids))
    for a in list(out):
        for dep in registry.AGENTS[a].depends_on:
            if dep not in out:
                out.insert(out.index(a), dep)
    return out


def tasks_for(conn: psycopg.Connection, kind: str, keys: list[str], agent_ids: list[str], as_of: date) -> list[Task]:
    tasks = []
    for a in with_dependencies(agent_ids):
        spec = registry.AGENTS[a]
        subjects = ["+".join(keys)] if a == "MF1" else keys  # MF1 looks at all the funds together
        for s in subjects:
            tasks.append(Task(a, s, spec.deadline_s, spec.depends_on, cached_run(conn, a, s, as_of, spec.freshness_days)))
    return tasks


def page_plan(conn: psycopg.Connection, kind: str, subject: str, as_of: date, agents: list[str] | None = None) -> Plan:
    key = resolve_stock(conn, subject) if kind == "stock" else resolve_fund(conn, subject)
    if key is None:
        return Plan(status="needs_clarification", kind=kind, message=f"no single {kind} matches {subject!r}")
    agent_ids = agents or registry.PAGE_AGENTS[kind]
    return Plan(status="ready", kind=kind, subjects=[key], tasks=tasks_for(conn, kind, [key], agent_ids, as_of), intent="page")


class AskIntent(BaseModel):
    intent: Literal["stock_research", "fund_research", "unsupported"]
    subjects: list[str] = Field(description="stocks or funds named in the question, as written (symbol, ISIN, "
                                            "AMFI code or full name); at most 5")
    agents: list[AgentIdLit] = Field(description="specialists needed to answer; empty for the default set")


ASK_SYSTEM = (
    "You route questions for an Indian investment-research site. Specialists: M1 price and technicals, M2 "
    "fundamentals, M3 forensic red flags, M4 filings and concalls, M5 news and sentiment, M6 peers and sector, M7 "
    "valuation ranges, MF1 fund portfolio overlap and costs, MF2 fund returns vs category and downside capture, "
    "MF3 closet-indexer check. Covered: NSE-listed stocks and Indian mutual fund schemes. Not covered: personal "
    "portfolios, tax, F&O, family finances, crypto, predictions. Pick the smallest set of specialists that "
    "answers the question."
)


def ask_plan(conn: psycopg.Connection, question: str, as_of: date) -> Plan:
    try:
        intent = call_json("strong", ASK_SYSTEM, question, AskIntent).value
    except LLMError as e:
        return Plan(status="needs_clarification", message=f"{PLAN_FAILED}: {e}")
    if intent.intent == "unsupported":
        return Plan(status="unsupported", message=UNSUPPORTED_MESSAGE, intent=intent.intent)
    kind = "stock" if intent.intent == "stock_research" else "fund"
    resolver = resolve_stock if kind == "stock" else resolve_fund
    resolved = {s: resolver(conn, s) for s in intent.subjects[:MAX_SUBJECTS]}
    if not resolved or None in resolved.values():
        unknown = [s for s, k in resolved.items() if k is None] or ["(none named)"]
        return Plan(status="needs_clarification", kind=kind, intent=intent.intent,
                    message=f"Which {kind} do you mean? Not found: {', '.join(unknown)}")
    keys = list(dict.fromkeys(resolved.values()))
    agent_ids = [a for a in intent.agents if a in registry.AGENTS and registry.AGENTS[a].kind == kind] or registry.PAGE_AGENTS[kind]
    return Plan(status="ready", kind=kind, subjects=keys, tasks=tasks_for(conn, kind, keys, agent_ids, as_of), intent=intent.intent)


# --- dispatch -------------------------------------------------------------------------------------------

def _cached(conn: psycopg.Connection, task: Task) -> Outcome:
    env, public, as_of = conn.execute(
        """SELECT o.envelope, o.public_ok, (r.as_of AT TIME ZONE 'Asia/Kolkata')::date
           FROM agent_outputs o JOIN agent_runs r ON r.id = o.run_id WHERE o.run_id = %s""", (task.cached_run_id,)).fetchone()
    return Outcome(task, "cached", Envelope.model_validate(env), public, as_of, run_id=task.cached_run_id)


def _run(conn: psycopg.Connection, task: Task, as_of: date) -> Outcome:
    try:
        env = registry.AGENTS[task.agent_id].run(conn, task.subject, as_of)
    except Exception as e:  # the agent run is logged as failed by run_logged; the report shows the gap
        return Outcome(task, "failed", run_as_of=as_of, error=f"{type(e).__name__}: {e}")
    (run_id,) = conn.execute("SELECT max(id) FROM agent_runs WHERE agent_id = %s AND subject = %s AND status = 'ok'",
                             (task.agent_id, task.subject)).fetchone()
    return Outcome(task, "ok", env, is_public(conn, env), as_of, run_id=run_id)


def dispatch(plan: Plan, conn: psycopg.Connection, as_of: date, connect=None) -> dict[tuple[str, str], Outcome]:
    """Run uncached tasks in dependency waves. With `connect` (a factory for new autocommit connections) each
    wave runs in parallel threads with the wave's largest time budget; without it tasks run inline on `conn`.
    ponytail: threads in-process; move to the Redis/Celery queue when the nightly batch needs more workers."""
    out: dict[tuple[str, str], Outcome] = {}
    pending = list(plan.tasks)
    while pending:
        attempted = {k[0] for k in out}  # a dependency counts once it was tried; the dependant reports any gap
        wave = [t for t in pending if all(d in attempted for d in t.depends_on if any(p.agent_id == d for p in plan.tasks))]
        if not wave:  # unreachable unless a dependency cycle sneaks into the registry
            wave = pending
        for t in wave:
            pending.remove(t)
        for t in [t for t in wave if t.cached_run_id]:
            out[(t.agent_id, t.subject)] = _cached(conn, t)
        todo = [t for t in wave if not t.cached_run_id]
        if not todo:
            continue
        if connect is None:
            for t in todo:
                out[(t.agent_id, t.subject)] = _run(conn, t, as_of)
            continue
        pool = ThreadPoolExecutor(max_workers=len(todo))

        def work(t):
            with connect() as c:
                return _run(c, t, as_of)

        started = time.monotonic()
        futures = {pool.submit(work, t): t for t in todo}
        for f, t in sorted(futures.items(), key=lambda ft: ft[1].deadline_s):  # each task gets its own budget
            finished, _ = wait([f], timeout=max(0.0, t.deadline_s - (time.monotonic() - started)))
            out[(t.agent_id, t.subject)] = f.result() if finished else Outcome(
                t, "timed_out", run_as_of=as_of, error=f"no result within {t.deadline_s}s")
        pool.shutdown(wait=False, cancel_futures=True)
    return out
