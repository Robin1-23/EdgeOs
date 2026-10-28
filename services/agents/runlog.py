"""agent_runs / agent_outputs logging shared by every specialist agent (CLAUDE.md: log each agent run)."""

import time
from collections.abc import Callable
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from services.agents.envelope import Envelope


def run_logged(conn: psycopg.Connection, agent_id: str, subject: str, as_of: datetime, compute: Callable[[], Envelope]) -> Envelope:
    """Log a 'running' row, compute, then mark ok (+ store the envelope) or failed (+ re-raise).
    cost_usd is 0 until an agent calls an LLM."""
    started = time.monotonic()
    (run_id,) = conn.execute(
        "INSERT INTO agent_runs (agent_id, subject, as_of, status) VALUES (%s, %s, %s, 'running') RETURNING id",
        (agent_id, subject, as_of),
    ).fetchone()
    try:
        env = compute()
    except Exception as e:
        conn.execute("UPDATE agent_runs SET status = 'failed', error = %s, duration_ms = %s, cost_usd = 0 WHERE id = %s",
                     (f"{type(e).__name__}: {e}", int((time.monotonic() - started) * 1000), run_id))
        raise
    conn.execute("UPDATE agent_runs SET status = 'ok', duration_ms = %s, cost_usd = 0 WHERE id = %s",
                 (int((time.monotonic() - started) * 1000), run_id))
    conn.execute("INSERT INTO agent_outputs (run_id, envelope) VALUES (%s, %s)", (run_id, Jsonb(env.model_dump(mode="json"))))
    return env
