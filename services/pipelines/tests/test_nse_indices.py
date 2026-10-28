from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from services.pipelines import nse_indices as idx
from services.pipelines.common import PipelineError
from services.pipelines.tests.fakes import FakeAdapter

FIX = Path(__file__).parent / "fixtures/nse_indices"
F2026 = (FIX / "ind_close_all_27102026.csv").read_bytes()
F2016 = (FIX / "ind_close_all_27102016.csv").read_bytes()
D2026, D2016 = date(2026, 10, 27), date(2016, 10, 27)


def by_name(parsed):
    return {r[0]: r for r in parsed.rows}


def test_parse_known_values():
    p = idx.parse(F2026, D2026)
    n = by_name(p)
    assert n["Nifty 50"] == (
        "Nifty 50", Decimal("22708.8"), Decimal("22829.75"), Decimal("22636.85"), Decimal("22749.75"),
        400951492, Decimal("33469.97"), Decimal("19.07"), Decimal("2.79"), Decimal("1.43"),
    )
    assert n["Nifty50 Dividend Points"][1:5] == (None, None, None, Decimal("204.22"))  # close-only series kept
    assert n["India VIX"][5:] == (None,) * 5
    assert p.skipped == {"bad_close": 1, "bad_ohlc": 1, "duplicate_index": 1}  # the fake second "Nifty 50" loses
    assert len(p.rows) == 6
    assert by_name(idx.parse(F2016, D2016))["Nifty 50"][4] == Decimal("8615.25")


def test_parse_rejects_wrong_day_and_header():
    with pytest.raises(PipelineError, match="expected 2026-10-26"):
        idx.parse(F2026, date(2026, 10, 26))
    with pytest.raises(PipelineError, match="header"):
        idx.parse(b"<!DOCTYPE html>", D2026)


def test_load_idempotent_and_requires_nifty(db_conn):
    p = idx.parse(F2026, D2026)
    assert idx.load(db_conn, p, min_rows=0)["indices_inserted"] == 6
    again = idx.load(db_conn, p, min_rows=0)
    assert (again["indices_inserted"], again["indices_updated"]) == (0, 0)
    with pytest.raises(PipelineError, match="partial"):
        idx.load(db_conn, p)
    p.rows = [r for r in p.rows if r[0] != "Nifty 50"]
    with pytest.raises(PipelineError, match="Nifty 50 missing"):
        idx.load(db_conn, p, min_rows=0)


def test_run_day_weekend_and_holiday(db_conn):
    adapter = FakeAdapter()
    assert idx.run_day(db_conn, date(2026, 10, 25), adapter)["status"] == "weekend"
    assert idx.run_day(db_conn, date(2026, 10, 20), adapter)["status"] == "no_file"
    assert adapter.calls == [("index_closes", date(2026, 10, 20))]  # weekends never asked for
