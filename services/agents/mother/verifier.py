"""Mother agent, verifier: re-run every reported number against its source data before it reaches a report.

Each agent is recomputed from the database with no logging and no LLM calls (M4/M5 use their cached labels).
A metric that differs beyond tolerance, or cannot be reproduced, is dropped; findings that cite a dropped metric
go with it (CLAUDE.md rule 4), and so do flags that point at it. Every check is returned for verification_log.
"""

import math
from dataclasses import dataclass
from datetime import date

import psycopg

from services.agents import registry
from services.agents.envelope import Envelope

REL_TOL, ABS_TOL = 1e-6, 1e-9  # numbers are computed by code, so anything beyond float noise is a real change


@dataclass(frozen=True)
class Check:
    agent_id: str
    metric_id: str
    claim: str
    reported: str
    recomputed: str | None
    dropped: bool
    reason: str | None


def same(a, b) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return a == b


def verify(conn: psycopg.Connection, agent_id: str, subject: str, as_of: date, env: Envelope) -> tuple[Envelope, list[Check]]:
    """(envelope with unverified metrics and everything citing them removed, all checks)."""
    try:
        fresh = {m.id: m.value for m in registry.AGENTS[agent_id].recompute(conn, subject, as_of).metrics}
        failure = None
    except Exception as e:
        fresh, failure = {}, f"recompute failed: {type(e).__name__}: {e}"
    checks, keep = [], set()
    for m in env.metrics:
        if failure or m.id not in fresh:
            checks.append(Check(agent_id, m.id, m.name, str(m.value), None, True, failure or "not reproducible from current data"))
        elif not same(m.value, fresh[m.id]):
            checks.append(Check(agent_id, m.id, m.name, str(m.value), str(fresh[m.id]), True, "mismatch beyond tolerance"))
        else:
            checks.append(Check(agent_id, m.id, m.name, str(m.value), str(fresh[m.id]), False, None))
            keep.add(m.id)
    findings = [f for f in env.findings if set(f.metric_ids) <= keep]
    flags = [f for f in env.flags if set(f.metric_ids) <= keep]
    verified = env.model_copy(update={"metrics": [m for m in env.metrics if m.id in keep], "findings": findings, "flags": flags})
    return Envelope.model_validate(verified.model_dump()), checks
