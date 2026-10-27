"""Applies 001_core.sql in a throwaway schema and inserts a row per table. Needs `docker compose up db`."""

import os
from pathlib import Path

import psycopg
import pytest

URL = os.environ.get("DATABASE_URL", "postgresql://edgeos:edgeos@localhost:5433/edgeos")
SQL = (Path(__file__).parent / "migrations/001_core.sql").read_text()


@pytest.fixture
def cur():
    try:
        conn = psycopg.connect(URL, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running")
    with conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA schema_test; SET search_path TO schema_test, public")
        cur.execute(SQL)
        yield cur
        conn.rollback()


def test_core_tables_accept_rows(cur):
    cur.execute("INSERT INTO securities (isin, nse_symbol, name) VALUES ('INE002A01018', 'RELIANCE', 'Reliance') RETURNING id")
    sid = cur.fetchone()[0]
    cur.execute("INSERT INTO prices_daily VALUES (%s, 'NSE', '2026-10-27', 100, 105, 99, 104, 1000, 104000)", (sid,))
    cur.execute(
        "INSERT INTO filings (security_id, exchange, filed_at, category, title, url)"
        " VALUES (%s, 'NSE', now(), 'results', 'Q2', 'https://x/1') RETURNING id",
        (sid,),
    )
    fid = cur.fetchone()[0]
    cur.execute("INSERT INTO fundamentals_quarterly VALUES (%s, '2026-09-30', 'Q', true, 'revenue', 1, 'INR cr', %s)", (sid, fid))
    cur.execute("INSERT INTO fund_schemes VALUES ('120503', 'Fund', 'AMC', 'Flexi Cap', 'direct', 'NIFTY 500 TRI')")
    cur.execute("INSERT INTO fund_nav VALUES ('120503', '2026-10-27', 85.1234)")
    cur.execute("INSERT INTO fund_holdings VALUES ('120503', '2026-09-01', 'INE002A01018', 8.5, 1e7)")
    cur.execute("INSERT INTO agent_runs (agent_id, subject, as_of, status) VALUES ('M1', 'RELIANCE', now(), 'ok') RETURNING id")
    rid = cur.fetchone()[0]
    cur.execute("INSERT INTO agent_outputs VALUES (%s, '{}')", (rid,))
    cur.execute("INSERT INTO reports (subject, as_of, report) VALUES ('RELIANCE', '2026-10-27', '{}') RETURNING id")
    cur.execute("INSERT INTO verification_log (report_id, run_id, claim, dropped) VALUES (%s, %s, 'c', false)", (cur.fetchone()[0], rid))


def test_rejects_bad_ohlc(cur):
    cur.execute("INSERT INTO securities (isin, nse_symbol, name) VALUES ('INE002A01018', 'RELIANCE', 'R') RETURNING id")
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("INSERT INTO prices_daily VALUES (%s, 'NSE', '2026-10-27', 100, 99, 98, 101, 1, null)", (cur.fetchone()[0],))
