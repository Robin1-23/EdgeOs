import os

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
