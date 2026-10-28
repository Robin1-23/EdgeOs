from datetime import date, datetime

import pytest

from services.agents.desks.markets import m2_fundamentals as m2
from services.pipelines import fundamentals
from services.pipelines.tests.fakes import FakeAdapter

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=m2.IST)
ISIN = "INE000000011"

# Synthetic company ACME, consolidated, FY16..FY26 (March year-ends), in INR crore. Chosen so every metric
# below can be worked out by hand.
REVENUE = dict(zip(range(2016, 2027), [50, 58, 66, 75, 87, 100, 120, 150, 165, 180, 200]))
FY26 = {"other_income": 4, "depreciation": 8, "finance_cost": 3, "exceptional_items": -2, "pbt": 27, "tax": 6, "pat": 21,
        "pat_owners": 20, "equity_total": 110, "equity_owners": 100, "borrowings_current": 10, "borrowings_noncurrent": 30,
        "cfo": 17, "capex": 9}
FY25 = {"pat": 19, "equity_total": 90, "equity_owners": 80, "borrowings_current": 10, "borrowings_noncurrent": 20, "cfo": 16}
FY24 = {"pat": 17.5, "cfo": 15}
QUARTERS = {date(2024, 12, 31): (40, 4), date(2025, 3, 31): (45, 4), date(2025, 6, 30): (45, 5), date(2025, 9, 30): (46, 5),
            date(2025, 12, 31): (48, 5), date(2026, 3, 31): (52, 5), date(2026, 6, 30): (52, 6), date(2026, 9, 30): (54, 6)}


def acme_records(consolidated=True, years=range(2016, 2027)):
    recs = []
    for y in years:
        fy = date(y, 3, 31)
        recs += [(ISIN, fy, "A", consolidated, "revenue_ops", REVENUE[y]), (ISIN, fy, "A", consolidated, "pat_owners", REVENUE[y] / 10)]
        extra = {2026: FY26, 2025: FY25, 2024: FY24}.get(y, {})
        recs += [(ISIN, fy, "A", consolidated, k, v) for k, v in extra.items() if k != "pat_owners"]
    for q, (rev, pat) in QUARTERS.items():
        recs += [(ISIN, q, "Q", consolidated, "revenue_ops", rev), (ISIN, q, "Q", consolidated, "pat_owners", pat)]
    return recs


def rows_of(records):
    return [(pe, pt, c, item, float(v)) for _, pe, pt, c, item, v in fundamentals.build(records).rows]


# --- formulas, hand-worked ------------------------------------------------------------------------------

def test_cagr():
    assert m2.cagr(150, 200, 3) == pytest.approx((200 / 150) ** (1 / 3) - 1)  # 0.100642
    assert m2.cagr(100, 200, 5) == pytest.approx(0.148698, abs=1e-6)  # 2 ** (1/5) - 1
    assert m2.cagr(-5, 10, 3) is None and m2.cagr(5, -1, 3) is None


def test_ebitda_ebit_and_capital():
    # 27 - (-2) + 3 + 8 - 4 = 36 ; EBIT 27 + 2 + 3 = 32 ; capital employed 110 + 10 + 30 = 150
    assert m2.ebitda(FY26) == 36 and m2.ebit(FY26) == 32 and m2.capital_employed(FY26) == 150
    assert m2.ebitda({"pbt": 1}) is None


def test_avg_return():
    assert m2.avg_return(20, 100, 80) == pytest.approx(20 / 90)  # ROE on average equity
    assert m2.avg_return(20, 100, None) is None and m2.avg_return(5, -10, 5) is None


def test_ttm_yoy_needs_8_consecutive_quarters():
    q = {d: {"revenue_ops": r} for d, (r, _) in QUARTERS.items()}
    # prior 4: 40+45+45+46 = 176; last 4: 48+52+52+54 = 206 -> 206/176 - 1 = 0.170455
    assert m2.ttm_yoy(q, "revenue_ops") == (206, 176, pytest.approx(0.170455, abs=1e-6))
    del q[date(2025, 9, 30)]
    assert m2.ttm_yoy(q, "revenue_ops") is None


def test_revenue_up_years():
    annual = {date(y, 3, 31): {"revenue_ops": v} for y, v in zip(range(2020, 2026), [10, 11, 10, 12, 13, 14])}
    assert m2.revenue_up_years(annual, sorted(annual)) == 4  # one dip in 2022
    assert m2.revenue_up_years(annual, sorted(annual)[1:]) is None  # only 5 years


# --- envelope -------------------------------------------------------------------------------------------

def test_compute_full_company():
    env = m2.compute(ISIN, "ACME", rows_of(acme_records() + acme_records(consolidated=False, years=[2026])), AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert v["revenue"] == 200 and v["pat"] == 20
    assert v["revenue_cagr_3y"] == pytest.approx(0.100642, abs=1e-6)
    assert v["revenue_cagr_5y"] == v["pat_cagr_5y"] == pytest.approx(0.148698, abs=1e-6)
    assert v["revenue_cagr_10y"] == pytest.approx(0.148698, abs=1e-6)  # 50 -> 200 over 10 years: 4 ** 0.1 - 1
    assert v["revenue_ttm"] == 206 and v["revenue_ttm_yoy"] == pytest.approx(0.170455, abs=1e-6)
    assert v["pat_ttm_yoy"] == pytest.approx(22 / 18 - 1)  # quarters 5+5+6+6 vs 4+4+5+5
    assert v["ebitda"] == 36 and v["ebitda_margin"] == pytest.approx(0.18)  # 36 / 200
    assert v["pat_margin"] == pytest.approx(0.105)  # 21 / 200
    assert v["roe"] == pytest.approx(0.222222, abs=1e-6)  # 20 / ((100 + 80) / 2)
    assert v["roce"] == pytest.approx(0.237037, abs=1e-6)  # 32 / ((150 + 120) / 2)
    assert v["debt_equity"] == pytest.approx(0.363636, abs=1e-6)  # 40 / 110
    assert v["cash_conversion_3y"] == pytest.approx(0.834783, abs=1e-6)  # (15+16+17) / (17.5+19+21)
    assert v["fcf"] == 8  # 17 - 9
    assert v["quality_score"] == 1.0 and all(v[f"quality_{k}"] == "pass" for k in m2.QUALITY_RULES)
    assert env.gaps == [] and env.confidence.level == "high" and "consolidated" in env.sources[0].ref
    assert env.flags == []


def test_standalone_fallback_and_short_history():
    env = m2.compute(ISIN, "ACME", rows_of(acme_records(consolidated=False, years=[2025, 2026])), AS_OF, NOW)
    items = {g.item for g in env.gaps}
    # only ROCE and leverage checks are computable from 2 years -> no score (needs 3 of 5)
    assert {"consolidated statements", "revenue 3-year CAGR", "growth quality score"} <= items
    assert any(f.reason == "figures are standalone, not consolidated" for f in env.flags)
    assert env.confidence.level == "low" and env.confidence.reason == "only 2 years of financials"


def test_negative_equity_loss_and_poor_cash_conversion_flags():
    recs = [r for r in acme_records() if not (r[1] == date(2026, 3, 31) and r[4] in ("equity_total", "pat_owners", "cfo"))]
    recs += [(ISIN, date(2026, 3, 31), "A", True, "equity_total", -5), (ISIN, date(2026, 3, 31), "A", True, "pat_owners", -3),
             (ISIN, date(2026, 3, 31), "A", True, "cfo", -20)]
    env = m2.compute(ISIN, "ACME", rows_of(recs), AS_OF, NOW)
    by_sev = {(f.severity, f.reason.split(" ")[0]) for f in env.flags}
    assert ("alert", "total") in by_sev and ("watch", "loss") in by_sev and ("watch", "3-year") in by_sev
    assert "debt_equity" not in {m.id for m in env.metrics}


def test_stale_and_no_revenue():
    stale = m2.compute(ISIN, "ACME", rows_of(acme_records()), date(2028, 10, 1), NOW)
    assert stale.confidence.reason == "stale financials"
    bank = m2.compute(ISIN, "BANK", rows_of([(ISIN, date(2026, 3, 31), "A", True, "pat", 10)]), AS_OF, NOW)
    assert bank.metrics == [] and bank.confidence.level == "low" and "banks" in bank.gaps[0].reason


# --- pipeline + run (DB) ----------------------------------------------------------------------------------

def test_build_validates_records():
    p = fundamentals.build([
        (ISIN, date(2026, 3, 31), "A", True, "revenue_ops", 200),
        (ISIN, date(2026, 3, 31), "A", True, "revenue_ops", 201),  # duplicate key
        (ISIN, date(2026, 3, 31), "H", True, "revenue_ops", 1),
        (ISIN, date(2026, 3, 31), "A", True, "ebitda", 1),  # derived, not a stored item
        ("XX1", date(2026, 3, 31), "A", True, "pat", 1),
        (ISIN, date(2026, 3, 31), "A", True, "pat", "N.A."),
    ])
    assert len(p.rows) == 1
    assert p.skipped == {"duplicate": 1, "bad_period_type": 1, "unknown_item": 1, "bad_isin": 1, "bad_value": 1}


def test_pipeline_and_run_end_to_end(db_conn):
    db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, last_seen) VALUES (%s, 'ACME', 'Acme', %s)", (ISIN, AS_OF))
    adapter = FakeAdapter(financials={ISIN: acme_records() + [("INE999999999", date(2026, 3, 31), "A", True, "pat", 1)]})
    result = fundamentals.run_security(db_conn, ISIN, adapter)
    assert result["skipped"] == {"unknown_security": 1} and result["rows_inserted"] > 0
    assert fundamentals.run_security(db_conn, ISIN, adapter)["rows_inserted"] == 0  # idempotent
    assert db_conn.execute("SELECT DISTINCT unit FROM fundamentals_quarterly WHERE item = 'revenue_ops'").fetchall() == [("INR cr",)]

    env = m2.run(db_conn, m2.M2Input(subject="ACME", as_of=AS_OF))
    assert {m.id: m.value for m in env.metrics}["roce"] == pytest.approx(0.237037, abs=1e-6)
    assert env.sources[0].providers == ["dev_local"]
    assert db_conn.execute("SELECT public_ok FROM agent_outputs").fetchone() == (False,)
