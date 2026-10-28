"""agent_runs / agent_outputs logging shared by every specialist agent (CLAUDE.md: log each agent run)."""

import time
from collections.abc import Callable
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from services.agents.envelope import Envelope

Providers = dict[str, list[str]]  # envelope source id -> data_sources names of the rows it read


def with_providers(env: Envelope, providers: Providers) -> Envelope:
    return env.model_copy(update={"sources": [s.model_copy(update={"providers": sorted(providers.get(s.id, []))})
                                              for s in env.sources]})


def is_public(conn: psycopg.Connection, env: Envelope) -> bool:
    """Publishable only if it has sources and every provider behind every source is public_ok. Fails closed."""
    names = {p for s in env.sources for p in s.providers}
    if not env.sources or any(not s.providers for s in env.sources):
        return False
    (public,) = conn.execute("SELECT count(*) FROM data_sources WHERE public_ok AND name = ANY(%s)", (list(names),)).fetchone()
    return public == len(names)


def run_logged(conn: psycopg.Connection, agent_id: str, subject: str, as_of: datetime,
               compute: Callable[[], tuple[Envelope, Providers]]) -> Envelope:
    """Log a 'running' row, compute, then mark ok (+ store the envelope with providers and public_ok) or
    failed (+ re-raise). cost_usd is 0 until an agent calls an LLM."""
    started = time.monotonic()
    (run_id,) = conn.execute(
        "INSERT INTO agent_runs (agent_id, subject, as_of, status) VALUES (%s, %s, %s, 'running') RETURNING id",
        (agent_id, subject, as_of),
    ).fetchone()
    try:
        env, providers = compute()
        env = with_providers(env, providers)
    except Exception as e:
        conn.execute("UPDATE agent_runs SET status = 'failed', error = %s, duration_ms = %s, cost_usd = 0 WHERE id = %s",
                     (f"{type(e).__name__}: {e}", int((time.monotonic() - started) * 1000), run_id))
        raise
    conn.execute("UPDATE agent_runs SET status = 'ok', duration_ms = %s, cost_usd = 0 WHERE id = %s",
                 (int((time.monotonic() - started) * 1000), run_id))
    conn.execute("INSERT INTO agent_outputs (run_id, envelope, public_ok) VALUES (%s, %s, %s)",
                 (run_id, Jsonb(env.model_dump(mode="json")), is_public(conn, env)))
    return env
