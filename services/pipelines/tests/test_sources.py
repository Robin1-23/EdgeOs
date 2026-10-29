import shutil
from datetime import date
from pathlib import Path

import pytest

from services.pipelines import nse_bhavcopy, nse_indices
from services.pipelines.sources import DevLocal, LicensedVendor, get_adapter
from services.pipelines.tests.fakes import FakeAdapter

FIX = Path(__file__).parent / "fixtures"
PIPELINES = Path(__file__).parents[1]


@pytest.fixture
def raw_dir(tmp_path):
    """A RAW_DATA_DIR laid out the way dev_local expects, filled from the test fixtures."""
    for folder, name in (("nse", "BhavCopy_NSE_CM_0_0_0_20261027_F_0000.csv.zip"), ("nse", "cm27OCT2016bhav.csv.zip"),
                         ("nse_indices", "ind_close_all_27102026.csv")):
        (tmp_path / folder).mkdir(exist_ok=True)
        shutil.copy(FIX / folder / name, tmp_path / folder / name)
    (tmp_path / "amfi").mkdir()
    shutil.copy(FIX / "amfi/NAVAll_sample.txt", tmp_path / "amfi/NAVAll_2026-10-27.txt")
    (tmp_path / "amfi_history").mkdir()
    shutil.copy(FIX / "amfi/NAVHistory_sample.txt", tmp_path / "amfi_history/NAVHistory_2026-10.txt")
    return tmp_path


def test_dev_local_reads_cached_files_for_both_nse_formats(raw_dir):
    dev = DevLocal(raw_dir)
    assert dev.bhavcopy_filename(date(2024, 7, 5)) == "cm05JUL2024bhav.csv.zip"
    assert dev.bhavcopy_filename(date(2024, 7, 8)) == "BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
    assert len(dev.eod_prices(date(2026, 10, 27)).rows) == 8
    assert len(dev.eod_prices(date(2016, 10, 27)).rows) == 5
    assert dev.eod_prices(date(2026, 10, 26)) is None  # not downloaded -> None, never a network call
    assert "Nifty 50" in {r[0] for r in dev.index_closes(date(2026, 10, 27)).rows}
    assert len(dev.fund_navs_latest(date(2026, 10, 27)).navs) == 11
    assert dev.fund_navs_latest(date(2026, 10, 28)) is None
    assert len(dev.fund_navs_month(2026, 10, date(2026, 10, 28)).navs) == 10
    assert dev.fund_navs_month(2026, 9, date(2026, 10, 28)) is None
    assert dev.fundamentals("INE002A01018") is None  # no permitted financials source in dev
    assert dev.classification() is None
    assert dev.fund_holdings(2026, 9) is None and dev.index_constituents(2026, 9) is None and dev.expense_ratios(2026, 9) is None
    assert dev.shareholding("INE002A01018") is None and dev.corporate_events("INE002A01018") is None
    assert dev.filings(date(2026, 1, 1)) is None
    assert dev.news(date(2026, 1, 1)) is None


def test_licensed_vendor_stub_fails_loudly():
    vendor = LicensedVendor("cmots")
    for call in (lambda: vendor.eod_prices(date(2026, 10, 27)), lambda: vendor.index_closes(date(2026, 10, 27)),
                 lambda: vendor.fund_navs_latest(date(2026, 10, 27)), lambda: vendor.fund_navs_month(2026, 10, date(2026, 10, 28)),
                 lambda: vendor.fundamentals("INE002A01018"), vendor.classification,
                 lambda: vendor.fund_holdings(2026, 9), lambda: vendor.index_constituents(2026, 9),
                 lambda: vendor.expense_ratios(2026, 9), lambda: vendor.shareholding("INE002A01018"),
                 lambda: vendor.corporate_events("INE002A01018"), lambda: vendor.filings(date(2026, 1, 1)),
                 lambda: vendor.news(date(2026, 1, 1))):
        with pytest.raises(NotImplementedError, match="cmots"):
            call()


def test_get_adapter(monkeypatch):
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    assert get_adapter().name == "dev_local"
    monkeypatch.setenv("DATA_SOURCE", "licensed_vendor")
    assert get_adapter().name == "licensed_vendor"
    with pytest.raises(ValueError, match="unknown data source"):
        get_adapter("scraper")


def test_no_pipeline_module_downloads():
    for path in PIPELINES.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "urllib" not in text and "requests" not in text and "http_get" not in text, path.name


def test_rows_carry_their_source_and_a_vendor_load_replaces_dev(db_conn, monkeypatch):
    monkeypatch.setattr(nse_bhavcopy, "MIN_ROWS", 0)
    monkeypatch.setattr(nse_indices, "MIN_ROWS", 0)
    day = date(2026, 10, 27)
    udiff = (FIX / "nse/BhavCopy_NSE_CM_0_0_0_20261027_F_0000.csv.zip").read_bytes()
    indices = (FIX / "nse_indices/ind_close_all_27102026.csv").read_bytes()

    nse_bhavcopy.run_day(db_conn, day, FakeAdapter("dev_local", prices={day: udiff}))
    nse_indices.run_day(db_conn, day, FakeAdapter("dev_local", indices={day: indices}))
    sources = lambda t: {s for (s,) in db_conn.execute(f"SELECT DISTINCT source FROM {t}")}  # noqa: E731
    assert sources("prices_daily") == sources("securities") == sources("index_prices") == {"dev_local"}

    result = nse_bhavcopy.run_day(db_conn, day, FakeAdapter("licensed_vendor", prices={day: udiff}))
    assert result["prices_updated"] == 8  # same values, new provenance
    assert sources("prices_daily") == sources("securities") == {"licensed_vendor"}
    assert db_conn.execute("SELECT public_ok FROM data_sources WHERE name = 'licensed_vendor'").fetchone() == (False,)
