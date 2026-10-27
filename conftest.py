import psycopg
import pytest

from db import DATABASE_URL
from db.migrate import migrate


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
