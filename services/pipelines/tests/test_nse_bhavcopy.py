from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from services.pipelines import nse_bhavcopy as nse
from services.pipelines.common import PipelineError
from services.pipelines.tests.fakes import FakeAdapter

FIX = Path(__file__).parent / "fixtures/nse"
UDIFF = (FIX / "BhavCopy_NSE_CM_0_0_0_20261027_F_0000.csv.zip").read_bytes()
OLD = (FIX / "cm27OCT2016bhav.csv.zip").read_bytes()
D2026, D2016 = date(2026, 10, 27), date(2016, 10, 27)
NIFTYBEES_2016, NIFTYBEES_2026 = "INF732E01011", "INF204KB14I2"  # ETF changed AMC (and ISIN) in between


def by_symbol(parsed):
    return {r[1]: r for r in parsed.rows}


def test_parse_udiff_known_values():
    p = nse.parse(UDIFF, D2026)
    s = by_symbol(p)
    assert s["RELIANCE"] == (
        "INE002A01018", "RELIANCE", "EQ", "RELIANCE INDUSTRIES LTD",
        Decimal("1195.00"), Decimal("1202.60"), Decimal("1184.00"), Decimal("1196.20"), 13067787, Decimal("15599937394.40"),
    )
    assert sorted(s) == ["3IINFOLTD", "AARADHYA", "AGSTRA", "AGUL", "INFY", "NIFTYBEES", "RELIANCE", "TCS"]
    assert s["INFY"][2] == "EQ"  # BE duplicate of the same ISIN lost on series priority
    assert p.skipped == {
        "non_equity_series": 3, "non_positive_price": 1, "bad_ohlc": 1,
        "bad_isin_or_symbol": 1, "bad_volume": 1, "duplicate_isin": 1,
    }


def test_parse_old_format_same_shape_no_name():
    p = nse.parse(OLD, D2016)
    s = by_symbol(p)
    assert s["RELIANCE"] == (
        "INE002A01018", "RELIANCE", "EQ", None,
        Decimal("1051.1"), Decimal("1053.9"), Decimal("1043.4"), Decimal("1048.7"), 2935574, Decimal("3078514158.3"),
    )
    assert s["NIFTYBEES"][0] == NIFTYBEES_2016 and s["ALPA"][2] == "BE"
    assert len(p.rows) == 5 and p.skipped == {"non_equity_series": 1}


def test_parse_rejects_wrong_day_and_garbage():
    with pytest.raises(PipelineError, match="expected 2026-10-26"):
        nse.parse(UDIFF, date(2026, 10, 26))
    with pytest.raises(PipelineError, match="zip"):
        nse.parse(b"<!DOCTYPE html>", D2026)


def security(conn, isin):
    return conn.execute("SELECT nse_symbol, name, last_seen FROM securities WHERE isin = %s", (isin,)).fetchone()


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_load_is_idempotent(db_conn):
    p = nse.parse(UDIFF, D2026)
    first = nse.load(db_conn, p, min_rows=0)
    assert (first["securities_inserted"], first["prices_inserted"]) == (8, 8)
    second = nse.load(db_conn, p, min_rows=0)
    assert [second[k] for k in ("securities_inserted", "securities_updated", "prices_inserted", "prices_updated")] == [0] * 4
    assert count(db_conn, "prices_daily") == 8


def test_older_file_never_overwrites_newer_symbol_or_name(db_conn):
    nse.load(db_conn, nse.parse(UDIFF, D2026), min_rows=0)
    nse.load(db_conn, nse.parse(OLD, D2016), min_rows=0)
    assert security(db_conn, "INE002A01018") == ("RELIANCE", "RELIANCE INDUSTRIES LTD", D2026)
    assert security(db_conn, NIFTYBEES_2026)[0] == "NIFTYBEES"
    assert security(db_conn, NIFTYBEES_2016)[0] is None  # symbol belongs to the newer ISIN
    assert count(db_conn, "prices_daily") == 13


def test_symbol_moves_to_new_isin_and_name_fills_in(db_conn):
    nse.load(db_conn, nse.parse(OLD, D2016), min_rows=0)
    assert security(db_conn, "INE002A01018") == ("RELIANCE", None, D2016)
    nse.load(db_conn, nse.parse(UDIFF, D2026), min_rows=0)
    assert security(db_conn, "INE002A01018") == ("RELIANCE", "RELIANCE INDUSTRIES LTD", D2026)
    assert security(db_conn, NIFTYBEES_2016)[0] is None
    assert security(db_conn, NIFTYBEES_2026)[0] == "NIFTYBEES"
    history = db_conn.execute(
        "SELECT p.trade_date, s.isin FROM prices_daily p JOIN securities s ON s.id = p.security_id"
        " WHERE p.symbol = 'NIFTYBEES' ORDER BY 1"
    ).fetchall()
    assert history == [(D2016, NIFTYBEES_2016), (D2026, NIFTYBEES_2026)]


def test_load_refuses_partial_file(db_conn):
    with pytest.raises(PipelineError, match="partial"):
        nse.load(db_conn, nse.parse(UDIFF, D2026))
    assert count(db_conn, "securities") == 0


def test_backfill_weekends_holidays_and_failures(db_conn, monkeypatch):
    monkeypatch.setattr(nse, "MIN_ROWS", 0)
    adapter = FakeAdapter(prices={date(2026, 10, 23): b"<html>bad</html>", D2026: UDIFF})  # 26th: no data (holiday)

    results = nse.backfill(db_conn, date(2026, 10, 23), D2026, adapter)  # Fri .. Tue
    assert [(r["day"].day, r["status"]) for r in results] == [
        (23, "failed"), (24, "weekend"), (25, "weekend"), (26, "no_file"), (27, "loaded"),
    ]
    assert adapter.calls == [("eod_prices", d) for d in (date(2026, 10, 23), date(2026, 10, 26), D2026)]
    assert count(db_conn, "prices_daily") == 8
