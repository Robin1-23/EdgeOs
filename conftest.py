import os
import sys

import psycopg
import pytest

from db import DATABASE_URL
from db.migrate import migrate

# Tests never call a live LLM: importing db loaded the repo .env, so drop the real key before any test runs.
os.environ.pop("OPENAI_API_KEY", None)


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    """Every test replays recorded LLM responses; a missing recording fails instead of calling the API."""
    from services.agents.llm import client

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "recorded")
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    client.reset_provider()
    yield
    client.reset_provider()


@pytest.fixture
def db_conn():
    """Connection with all migrations applied in a throwaway schema; rolled back after the test."""
    try:
        conn = psycopg.connect(DATABASE_URL, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (docker compose up -d db)")
    with conn:
        conn.execute("CREATE SCHEMA schema_test; SET search_path TO schema_test, public")
        migrate(conn)
        yield conn
        conn.rollback()


# Formula catalogue check: every formula_id an agent's compute emits during the run must have a definition in
# services/agents/formulas.py, or the run fails. Records envelopes built inside desks/ modules (not test fakes).
_SEEN_FORMULAS: set[tuple[str, str]] = set()


def pytest_configure(config):
    from services.agents import envelope

    init = envelope.Envelope.__init__

    def recording(self, *args, **kwargs):
        init(self, *args, **kwargs)
        caller = sys._getframe(1).f_code.co_filename
        if f"{os.sep}desks{os.sep}" in caller and f"{os.sep}tests{os.sep}" not in caller:
            _SEEN_FORMULAS.update((self.agent_id, m.formula_id) for m in self.metrics)

    envelope.Envelope.__init__ = recording


def pytest_sessionfinish(session, exitstatus):
    from services.agents.formulas import describe

    missing = sorted(f"{a} {f}" for a, f in _SEEN_FORMULAS if describe(a, f) is None)
    if missing:
        print(f"\nformula ids with no definition in services/agents/formulas.py: {', '.join(missing)}", file=sys.stderr)
        session.exitstatus = 1
