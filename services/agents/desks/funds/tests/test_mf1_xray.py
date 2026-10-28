from datetime import date, datetime

import pytest
from pydantic import ValidationError

from services.agents.desks.funds import mf1_xray as mf1

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=mf1.IST)
SEP = date(2026, 9, 1)
A, B, C, D = "INE000000011", "INE000000029", "INE000000037", "INE000000045"
SECURITIES = {A: {"symbol": "AAA", "sector": "Financials"}, B: {"symbol": "BBB", "sector": "IT"},
              C: {"symbol": "CCC", "sector": "Financials"}, D: {"symbol": "DDD", "sector": None}}


def fund(code, holdings, amount=None, plan="direct", ter=1.0, counterpart=None, month=SEP, name=None):
    return {"amfi_code": code, "name": name or f"Fund {code}", "plan": plan, "option": "Growth", "amount": amount,
            "holdings": holdings, "month": month, "ter": ter, "counterpart": counterpart}


# --- formulas, hand-worked ------------------------------------------------------------------------------

def test_overlap():
    # common A and B: min(50, 40) + min(30, 40) = 70 -> 0.70
    assert mf1.overlap({A: 50, B: 30, C: 20}, {A: 40, B: 40, D: 20}) == (pytest.approx(0.70), 2)
    assert mf1.overlap({A: 100}, {B: 100}) == (0, 0)


def test_effective_exposure():
    # Rs 60k in {A 50, B 50}, Rs 40k in {A 25, C 75}: A = .6 x .5 + .4 x .25 = .40; B .30; C .30
    exp = mf1.effective_exposure([(60_000, {A: 50, B: 50}), (40_000, {A: 25, C: 75})])
    assert exp == {A: pytest.approx(0.40), B: pytest.approx(0.30), C: pytest.approx(0.30)}


def test_cost_gap():
    # 1 lakh, TER 1% vs 2%, 10% gross: 1.09^10 - 1.08^10 = 2.367364 - 2.158925 -> Rs 20,843.87
    assert mf1.cost_gap(100_000, 0.01, 0.02, 10, 0.10) == pytest.approx(20_843.87, abs=0.01)
    assert mf1.cost_gap(100_000, 0.01, 0.02, 20, 0.10) == pytest.approx(94_345.36, abs=0.01)  # 5.604411 - 4.660957
    assert mf1.cost_gap(100_000, 0.01, 0.02, 10, 0.0) == pytest.approx(8_730.93, abs=0.01)  # 0.904382 - 0.817073


def test_input_validation():
    with pytest.raises(ValidationError):
        mf1.MF1Input(funds=[], as_of=AS_OF)
    with pytest.raises(ValidationError):
        mf1.MF1Input(funds=[{"amfi_code": "1"}, {"amfi_code": "1"}], as_of=AS_OF)
    with pytest.raises(ValidationError):
        mf1.MF1Input(funds=[{"amfi_code": "1", "amount": -5}], as_of=AS_OF)
    assert mf1.MF1Input(funds=[{"amfi_code": "1"}, {"amfi_code": "2"}], as_of=AS_OF).subject == "1+2"


# --- envelope -------------------------------------------------------------------------------------------

def test_compute_two_funds_with_amounts():
    f1 = fund("1", {A: 50, B: 50}, amount=60_000, ter=1.0, counterpart={"amfi_code": "11", "plan": "regular", "ter": 2.0})
    f2 = fund("2", {A: 25, C: 75}, amount=40_000, plan="regular", ter=2.0, counterpart={"amfi_code": "22", "plan": "direct", "ter": 1.0})
    env = mf1.compute([f1, f2], SECURITIES, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert v["overlap_1_2"] == pytest.approx(0.25) and v["common_stocks_1_2"] == 1  # min(50, 25)
    assert v[f"stock_{A}"] == pytest.approx(0.40) and v["total_amount"] == 100_000
    assert v["sector_financials"] == pytest.approx(0.70) and v["sector_it"] == pytest.approx(0.30)  # A + C, B
    assert v["unclassified_weight"] == 0
    assert v["ter_gap_1"] == pytest.approx(0.01) and v["ter_gap_2"] == pytest.approx(0.01)
    # Rs 60,000 actual amount, 10% is not in COST_GROWTH; 12% over 10y: 60k x (1.11^10 - 1.10^10)
    assert v["cost_gap_1_10y_g12"] == pytest.approx(60_000 * (1.11 ** 10 - 1.10 ** 10))
    assert "Rs 60,000" in next(m.name for m in env.metrics if m.id == "cost_gap_1_10y_g12")
    assert all("assumption, not a forecast" in m.name for m in env.metrics if m.id.startswith("cost_gap_"))
    assert any(f.reason == "AAA is 40% of the combined holding" for f in env.flags)
    assert env.gaps == [] and env.confidence.level == "high"
    for f in env.flags:
        assert "switch" not in f.reason.lower() and "exit" not in f.reason.lower()


def test_overlap_only_without_amounts_and_plan_gaps():
    f1 = fund("1", {A: 60, B: 40}, counterpart="ambiguous")
    f2 = fund("2", {A: 55, B: 30, D: 15}, counterpart=None, ter=None)
    env = mf1.compute([f1, f2], SECURITIES, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert v["overlap_1_2"] == pytest.approx(0.85)  # min(60, 55) + min(40, 30)
    assert any("share 85% of their portfolios" in f.reason for f in env.flags)
    assert "total_amount" not in v
    reasons = {g.item: g.reason for g in env.gaps}
    assert reasons["effective exposure"].startswith("needs an amount")
    assert reasons["1 regular vs direct"] == "more than one matching plan"
    assert reasons["2 expense ratio"].startswith("no TER")
    assert env.confidence.level == "medium"


def test_illustrative_amount_and_stale_portfolio():
    f1 = fund("1", {A: 100}, counterpart={"amfi_code": "11", "plan": "regular", "ter": 2.0})
    f2 = fund("2", {A: 100}, month=date(2026, 5, 1))
    env = mf1.compute([f1, f2], SECURITIES, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert v["cost_gap_1_10y_g0"] == pytest.approx(8_730.93, abs=0.01)  # per Rs 1 lakh
    assert "per Rs 1 lakh" in next(m.name for m in env.metrics if m.id == "cost_gap_1_10y_g0")
    assert "overlap_1_2" not in v and "too old" in {g.item: g.reason for g in env.gaps}["2 portfolio"]
    none = mf1.compute([fund("3", {})], SECURITIES, AS_OF, NOW)
    assert none.confidence.level == "low"


# --- run (DB) -------------------------------------------------------------------------------------------

def test_run_pairs_plans_and_inherits_providers(db_conn):
    cat = "Equity Scheme - Flexi Cap Fund"
    for code, name, plan, opt in (("100001", "Alpha Flexi Cap", "direct", "Growth"), ("100002", "Alpha Flexi Cap", "regular", "Growth"),
                                  ("100003", "Alpha Flexi Cap", "regular", "IDCW"), ("200001", "Beta Flexi Cap", "direct", "Growth"),
                                  ("200002", "Beta Flexi Cap", "regular", "Growth Option"), ("200003", "Beta Flexi Cap", "regular", "GROWTH")):
        db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, option, scheme_type)"
                        " VALUES (%s, %s, 'AMC', %s, %s, %s, 'open')", (code, name, cat, plan, opt))
    for code, ter in (("100001", 0.8), ("100002", 1.9), ("200001", 0.7)):
        db_conn.execute("INSERT INTO fund_expense_ratios (amfi_code, as_of, ter_pct) VALUES (%s, '2026-09-30', %s)", (code, ter))
    for code, holdings in (("100001", {A: 50, B: 50}), ("200001", {A: 25, C: 75})):
        for isin, w in holdings.items():
            db_conn.execute("INSERT INTO fund_holdings (amfi_code, month, isin, weight_pct) VALUES (%s, %s, %s, %s)", (code, SEP, isin, w))
    for isin, s in SECURITIES.items():
        db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, sector, classification_source) VALUES (%s, %s, %s, %s, %s)",
                        (isin, s["symbol"], s["symbol"], s["sector"], "dev_local" if s["sector"] else None))

    env = mf1.run(db_conn, mf1.MF1Input(funds=[{"amfi_code": "100001", "amount": 60_000}, {"amfi_code": "200001", "amount": 40_000}],
                                        as_of=AS_OF))
    v = {m.id: m.value for m in env.metrics}
    assert v["ter_gap_100001"] == pytest.approx(0.011)  # 1.9% - 0.8%: the IDCW plan is not a counterpart
    assert {g.item: g.reason for g in env.gaps}["200001 regular vs direct"] == "more than one matching plan"
    assert v[f"stock_{A}"] == pytest.approx(0.40)
    assert all(s.providers == ["dev_local"] for s in env.sources)
    assert db_conn.execute("SELECT public_ok FROM agent_outputs").fetchone() == (False,)
