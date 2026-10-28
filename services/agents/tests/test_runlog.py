from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.agents.envelope import Confidence, Envelope, Metric, Source
from services.agents.runlog import is_public, run_logged, with_providers

T = datetime(2026, 10, 27, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))


def envelope(*source_ids: str) -> Envelope:
    return Envelope(
        agent_id="M1", subject="TEST", as_of=T, findings=[], flags=[], gaps=[],
        metrics=[Metric(id=f"m_{s}", name=s, value=1.0, formula_id="f", source_query_id=s) for s in source_ids],
        sources=[Source(id=s, kind="dataset", ref=s, retrieved_at=T) for s in source_ids],
        confidence=Confidence(level="high", reason="test"),
    )


@pytest.fixture
def vendor_public(db_conn):
    db_conn.execute("UPDATE data_sources SET public_ok = true WHERE name = 'licensed_vendor'")  # rolled back after the test
    return db_conn


def test_public_only_when_every_provider_is_public(vendor_public):
    conn = vendor_public
    env = envelope("prices", "nifty")
    assert is_public(conn, with_providers(env, {"prices": ["licensed_vendor"], "nifty": ["licensed_vendor"]}))
    assert not is_public(conn, with_providers(env, {"prices": ["licensed_vendor"], "nifty": ["dev_local", "licensed_vendor"]}))
    assert not is_public(conn, with_providers(env, {"prices": ["licensed_vendor"]}))  # nifty provider unknown
    assert not is_public(conn, with_providers(env, {"prices": ["scraped"], "nifty": ["licensed_vendor"]}))  # not a data source
    assert not is_public(conn, envelope())  # nothing cited


def test_run_logged_stores_providers_and_public_flag(vendor_public):
    conn = vendor_public
    run_logged(conn, "M1", "DEV", T, lambda: (envelope("prices"), {"prices": ["dev_local"]}))
    run_logged(conn, "M1", "LIC", T, lambda: (envelope("prices"), {"prices": ["licensed_vendor"]}))
    rows = dict(conn.execute("SELECT r.subject, o.public_ok FROM agent_runs r JOIN agent_outputs o ON o.run_id = r.id").fetchall())
    assert rows == {"DEV": False, "LIC": True}
    stored = conn.execute("SELECT o.envelope FROM agent_outputs o JOIN agent_runs r ON r.id = o.run_id WHERE r.subject = 'DEV'").fetchone()[0]
    assert stored["sources"][0]["providers"] == ["dev_local"]
