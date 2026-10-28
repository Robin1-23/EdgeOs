import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from services.agents.desks.funds import mf2_manager as mf2
from services.agents.desks.funds.categories import is_growth, sebi_category

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=mf2.IST)


def grow(annual: float, start: date = date(2016, 10, 1), end: date = AS_OF, step: int = 1, base: float = 100.0):
    """NAV compounding at `annual` per 365.25 days, one point every `step` days."""
    out, d = [], start
    while d <= end:
        out.append((d, base * (1 + annual) ** ((d - start).days / 365.25)))
        d += timedelta(days=step)
    return out


# --- categories -----------------------------------------------------------------------------------------

def test_sebi_category_merges_spellings_and_skips_old_names():
    assert sebi_category("Equity Scheme - Large Cap Fund") == sebi_category("Equity Schemes - Large Cap Fund") == "Large Cap"
    assert sebi_category("Equity Schemes - ELSS- Tax Saver Fund") == sebi_category("Equity Scheme - ELSS") == "ELSS"
    assert sebi_category("Hybrid Schemes - Balanced Advantage Fund/ Dynamic Asset Allocation") == "Balanced Advantage"
    assert sebi_category("Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage") == "Balanced Advantage"
    assert sebi_category("Solution Oriented Schemes ** - Retirement Fund") == "Retirement"
    assert sebi_category("Income/Debt Oriented Schemes - Liquid Fund") == "Liquid"
    assert sebi_category("Income/Debt Oriented Schemes - Short Term Fund") is None  # pre-2018, not SEBI's Short Duration
    assert sebi_category("Other Scheme - Index Funds") is None and sebi_category("Growth") is None and sebi_category(None) is None


def test_is_growth():
    assert all(is_growth(o) for o in ("Growth", "Growth Option", "GROWTH"))
    assert not any(is_growth(o) for o in ("IDCW", "Monthly IDCW", "Dividend", "", None))


# --- formulas, hand-worked ------------------------------------------------------------------------------

def test_value_on_respects_staleness():
    s = [(date(2026, 1, 1), 10.0), (date(2026, 1, 5), 11.0)]
    assert mf2.value_on(s, date(2026, 1, 7)) == 11.0  # last value on or before, 2 days old
    assert mf2.value_on(s, date(2026, 1, 13)) is None  # 8 days old
    assert mf2.value_on(s, date(2025, 12, 31)) is None  # before the series


def test_years_before_and_cagr():
    assert mf2.years_before(date(2024, 2, 29), 1) == date(2023, 2, 28)
    assert mf2.cagr(100, 121, 2) == pytest.approx(0.10)  # sqrt(1.21) - 1
    assert mf2.cagr(100, 110, 1) == pytest.approx(0.10)


def test_category_mean_skips_peers_without_data():
    end = date(2026, 6, 30)
    peers = {
        "a": [(date(2025, 6, 30), 100.0), (end, 110.0)],  # +10%
        "b": [(date(2025, 6, 30), 100.0), (end, 106.0)],  # +6%
        "c": [(date(2026, 1, 1), 100.0), (end, 150.0)],  # launched within the year: excluded
    }
    assert mf2.category_mean(peers, end, 1, min_peers=2) == (pytest.approx(0.08), 2)
    assert mf2.category_mean(peers, end, 1, min_peers=3) is None


def test_month_ends():
    assert mf2.month_ends(date(2026, 10, 27), 3) == [date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30)]
    assert mf2.month_ends(date(2026, 10, 31), 2) == [date(2026, 9, 30), date(2026, 10, 31)]


def test_rolling_vs_category():
    ends = [date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30)]
    starts = [mf2.years_before(e, 1) for e in ends]
    # fund 1y returns per window: +10%, +5%, +20%, 0%; every peer +8% in every window
    fund = sorted([(s, 100.0) for s in starts] + list(zip(ends, [110.0, 105.0, 120.0, 100.0])))
    peers = {str(i): sorted([(s, 100.0) for s in starts] + [(e, 108.0) for e in ends]) for i in range(5)}
    r = mf2.rolling_vs_category(fund, peers, date(2026, 10, 15), 1, windows=4)
    # beat in 2 of 4 windows; median of (0.10, 0.05, 0.20, 0.0) = (0.05 + 0.10) / 2; worst 0.0
    assert r == {"windows": 4, "beat_share": 0.5, "median": pytest.approx(0.075), "worst": 0.0}


def test_downside_capture():
    fund = [-0.02, 0.01, -0.04, 0.03]
    bench = [-0.04, 0.02, -0.08, 0.02]
    # down months (bench < 0): fund -2%, -4%; bench -4%, -8%
    # geometric means: fund sqrt(0.98 * 0.96) - 1 = -0.0300515; bench sqrt(0.96 * 0.92) - 1 = -0.0602128
    capture, k = mf2.downside_capture(fund, bench, min_down=2)
    assert capture == pytest.approx(0.0300515 / 0.0602128, abs=1e-6) and k == 2
    assert mf2.downside_capture(fund, bench, min_down=3) is None


# --- envelope -------------------------------------------------------------------------------------------

SCHEME = {"amfi_code": "100001", "name": "Test Flexi Cap - Direct Growth", "category": "Equity Scheme - Flexi Cap Fund",
          "plan": "direct", "option": "Growth"}


def bench_series():
    # monthly-alternating index (every 3rd month falls 3%) so downside capture has down months
    out, v, d = [], 1000.0, date(2016, 10, 1)
    while d <= AS_OF:
        out.append((d, v))
        d += timedelta(days=1)
        if d.day == 1:
            v *= 0.97 if d.month % 3 == 0 else 1.02
    return out


def test_compute_full_fund():
    fund = grow(0.15)
    peers = {str(i): grow(0.10) for i in range(6)}
    env = mf2.compute(SCHEME, fund, peers, bench_series(), AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    for n in (1, 3, 5):
        assert v[f"return_{n}y"] == pytest.approx(0.15, abs=2e-3)
        assert v[f"category_return_{n}y"] == pytest.approx(0.10, abs=2e-3) and v[f"category_peers_{n}y"] == 6
        assert v[f"rolling_{n}y_beat_share"] == 1.0
    assert v["rolling_5y_windows"] == 60
    assert "downside_capture_36m" in v and "downside_capture_60m" in v
    assert [g.item for g in env.gaps] == ["fund manager history"]
    assert env.confidence.level == "medium" and "price index, not TRI" in env.confidence.reason
    assert {s.id for s in env.sources} == {"nav", "peers", "benchmark"}


def test_compute_consistency_flag_when_rarely_above_category():
    env = mf2.compute(SCHEME, grow(0.05), {str(i): grow(0.10) for i in range(6)}, [], AS_OF, NOW)
    assert any(f.severity == "watch" and "rolling 3-year" in f.reason for f in env.flags)
    assert any(g.item == "downside capture" for g in env.gaps)  # no benchmark closes given


def test_compute_gaps_for_idcw_unmapped_and_stale():
    idcw = mf2.compute({**SCHEME, "option": "IDCW"}, grow(0.1), {}, [], AS_OF, NOW)
    assert idcw.metrics == [] and idcw.confidence.level == "low"
    unmapped = mf2.compute({**SCHEME, "category": "Other Scheme - Index Funds"}, grow(0.1), {}, [], AS_OF, NOW)
    assert "return_1y" in {m.id for m in unmapped.metrics} and "category_return_1y" not in {m.id for m in unmapped.metrics}
    assert any(g.item == "category comparison" for g in unmapped.gaps)
    stale = mf2.compute(SCHEME, grow(0.1, end=date(2026, 9, 1)), {}, [], AS_OF, NOW)
    assert stale.confidence.reason == "no current NAV"
    young = mf2.compute(SCHEME, grow(0.1, start=date(2025, 1, 1)), {}, [], AS_OF, NOW)
    assert {"3-year return", "5-year return"} <= {g.item for g in young.gaps}


# --- run + logging (DB) ---------------------------------------------------------------------------------

def test_run_loads_peers_from_db_and_logs(db_conn):
    schemes = [("100001", "Equity Scheme - Flexi Cap Fund", "direct", "Growth", 0.15)]
    schemes += [(f"10000{i}", "Equity Schemes - Flexi Cap Fund", "direct", "Growth Option", 0.10) for i in range(2, 8)]
    schemes += [("100008", "Equity Scheme - Flexi Cap Fund", "regular", "Growth", 0.0), ("100009", "Equity Scheme - Flexi Cap Fund", "direct", "IDCW", 0.0)]
    for code, cat, plan, opt, annual in schemes:
        db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, option, scheme_type)"
                        " VALUES (%s, %s, 'AMC', %s, %s, %s, 'open')", (code, code, cat, plan, opt))
        with db_conn.cursor().copy("COPY fund_nav (amfi_code, nav_date, nav) FROM STDIN") as cp:
            for d, v in grow(annual, start=date(2020, 10, 1), step=7, base=100 + annual):
                cp.write_row((code, d, round(v, 4)))

    env = mf2.run(db_conn, mf2.MF2Input(amfi_code="100001", as_of=AS_OF))
    v = {m.id: m.value for m in env.metrics}
    assert v["category_peers_1y"] == 7  # itself + 6 direct growth peers across both spellings; regular and IDCW excluded
    assert v["return_3y"] == pytest.approx(0.15, abs=3e-3)
    status, envelope = db_conn.execute(
        "SELECT r.status, o.envelope FROM agent_runs r JOIN agent_outputs o ON o.run_id = r.id WHERE r.agent_id = 'MF2'").fetchone()
    assert status == "ok" and mf2.Envelope.model_validate(envelope) == env
    with pytest.raises(LookupError):
        mf2.run(db_conn, mf2.MF2Input(amfi_code="999999", as_of=AS_OF))


# --- real data: Parag Parikh Flexi Cap Direct Growth (122639) vs Flexi Cap direct-growth peers -----------

def test_real_flexi_cap_matches_sql(db_conn):
    """Fixture: every open-ended Flexi Cap scheme (all plans/options) with NAVs in the 10 days before 27-Oct of
    2021, 2023, 2025 and 2026. Expected values were computed by an independent SQL query (power/avg over the same
    7-day NAV lookup, peers = direct plan + growth option)."""
    fix = Path(__file__).parent / "fixtures"
    for r in csv.DictReader(open(fix / "flexicap_schemes.csv", encoding="utf-8", newline="")):
        db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, option, scheme_type)"
                        " VALUES (%s, %s, 'AMC', %s, %s, %s, 'open')", (r["amfi_code"], r["name"], r["category"], r["plan"] or None, r["option"] or None))
    with db_conn.cursor().copy("COPY fund_nav (amfi_code, nav_date, nav) FROM STDIN") as cp:
        for r in csv.DictReader(open(fix / "flexicap_navs.csv", newline="")):
            cp.write_row((r["amfi_code"], r["nav_date"], r["nav"]))

    env = mf2.run(db_conn, mf2.MF2Input(amfi_code="122639", as_of=AS_OF))
    v = {m.id: m.value for m in env.metrics}
    expected = {1: (-0.0678373491, -0.0423387984, 42), 3: (0.1270452150, 0.1222522260, 36), 5: (0.1072189697, 0.0944826657, 27)}
    for n, (fund, cat, peers) in expected.items():
        assert v[f"return_{n}y"] == pytest.approx(fund, abs=1e-9)
        assert v[f"category_return_{n}y"] == pytest.approx(cat, abs=1e-9)
        assert v[f"category_peers_{n}y"] == peers
    assert any(g.item == "downside capture" for g in env.gaps)  # no Nifty 500 closes in this fixture
