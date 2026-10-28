from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest

from db import DATABASE_URL
from services.pipelines import amfi_nav, nightly, nse_bhavcopy, nse_indices
from services.pipelines.common import PipelineError
from services.pipelines.tests.fakes import FakeAdapter

FIX = Path(__file__).parent / "fixtures"
AMFI = (FIX / "amfi/NAVAll_sample.txt").read_text(encoding="utf-8")
UDIFF = (FIX / "nse/BhavCopy_NSE_CM_0_0_0_20261027_F_0000.csv.zip").read_bytes()
INDICES = (FIX / "nse_indices/ind_close_all_27102026.csv").read_bytes()
TODAY = date(2026, 10, 27)  # Tuesday; window 21..27 has weekdays 21, 22, 23, 26, 27


@pytest.fixture
def fakes(monkeypatch):
    """Adapter with data only for TODAY; earlier weekdays look like holidays / late files."""
    monkeypatch.setattr(amfi_nav, "MIN_SCHEMES", 0)
    for mod in (nse_bhavcopy, nse_indices):
        monkeypatch.setattr(mod, "MIN_ROWS", 0)
    return FakeAdapter(prices={TODAY: UDIFF}, indices={TODAY: INDICES}, navs_latest=AMFI)


def nse_calls(adapter):
    return [c for c in adapter.calls if c[0] != "fund_navs_latest"]


def runs(conn):
    return conn.execute(
        "SELECT pipeline, target_date, status, summary, error, finished_at IS NOT NULL, duration_ms IS NOT NULL"
        " FROM pipeline_runs ORDER BY id"
    ).fetchall()


def test_normal_night_runs_and_logs_both(db_conn, fakes):
    results = nightly.nightly(db_conn, TODAY, fakes)
    assert [(r["pipeline"], r["target_date"].day, r["status"]) for r in results] == [
        ("amfi_nav", 27, "loaded"),
        *[("nse_bhavcopy", d, "no_file") for d in (21, 22, 23, 26)],
        ("nse_bhavcopy", 27, "loaded"),
        *[("nse_indices", d, "no_file") for d in (21, 22, 23, 26)],
        ("nse_indices", 27, "loaded"),
    ]
    assert nightly.exit_code(results) == 0
    logged = runs(db_conn)
    assert len(logged) == 11 and all(r[5] and r[6] for r in logged)
    assert logged[0][3]["navs_inserted"] == 11 and logged[5][3]["prices_inserted"] == 8
    assert logged[-1][3]["indices_inserted"] == 6


def test_next_night_only_retries_days_not_loaded(db_conn, fakes):
    nightly.nightly(db_conn, TODAY, fakes)
    fakes.calls.clear()
    nightly.nightly(db_conn, TODAY, fakes)
    retried = [date(2026, 10, d) for d in (21, 22, 23, 26)]  # 27 already loaded; weekends never fetched
    assert nse_calls(fakes) == [("eod_prices", d) for d in retried] + [("index_closes", d) for d in retried]


def test_failure_in_one_pipeline_does_not_stop_the_other(db_conn, fakes, monkeypatch):
    def broken():
        raise PipelineError("AMFI file unreadable")

    fakes.navs_latest = broken
    results = nightly.nightly(db_conn, TODAY, fakes)
    assert results[0]["status"] == "failed" and results[-1]["status"] == "loaded"
    assert nightly.exit_code(results) == 1
    assert runs(db_conn)[0][2:5] == ("failed", None, "PipelineError: AMFI file unreadable")


def test_lock_blocks_concurrent_run(db_conn, fakes):
    with psycopg.connect(DATABASE_URL, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (nightly.LOCK_KEY,))
        results = nightly.nightly(db_conn, TODAY, fakes)
    assert results is None and nightly.exit_code(results) == 2
    assert runs(db_conn) == [] and fakes.calls == []


def test_today_is_ist():
    assert nightly.today_ist(datetime(2026, 10, 27, 19, 0, tzinfo=UTC)) == date(2026, 10, 28)  # 00:30 IST
    assert nightly.today_ist(datetime(2026, 10, 27, 18, 29, tzinfo=UTC)) == date(2026, 10, 27)
