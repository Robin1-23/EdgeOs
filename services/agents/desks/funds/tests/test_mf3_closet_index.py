from datetime import date, datetime

import pytest

from services.agents.desks.funds import mf3_closet_index as mf3
from services.agents.desks.funds.mf2_manager import month_ends
from services.pipelines import fund_disclosures
from services.pipelines.tests.fakes import FakeAdapter

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=mf3.IST)
SEP = date(2026, 9, 1)
SCHEME = {"amfi_code": "100001", "name": "Test Large Cap - Direct Growth", "category": "Equity Scheme - Large Cap Fund",
          "plan": "direct", "option": "Growth"}
INDEX = {"INE000000011": 40.0, "INE000000029": 40.0, "INE000000037": 20.0}


def series(wiggle: float):
    """Month-end benchmark closes rising 1% a month; the fund adds +-wiggle each month (alternating)."""
    ends = month_ends(AS_OF, 40)
    bench, fund, b, f = [], [], 1000.0, 100.0
    for i, d in enumerate(ends):
        bench.append((d, b))
        fund.append((d, f))
        b *= 1.01
        f *= 1.01 + (wiggle if i % 2 else -wiggle)
    return fund, bench


# --- formulas, hand-worked ------------------------------------------------------------------------------

def test_active_share():
    fund = {"A": 50.0, "B": 30.0, "C": 20.0}
    index = {"A": 40.0, "B": 40.0, "D": 20.0}
    # |50-40| + |30-40| + |20-0| + |0-20| = 60 -> 60 / 200 = 0.30
    assert mf3.active_share(fund, index) == pytest.approx(0.30)
    assert mf3.active_share(fund, {"A": 20.0, "B": 20.0, "D": 10.0}) == pytest.approx(0.30)  # index normalised to 100
    # 20% uninvested: |50-40| + |30-40| + |0-20| = 40, plus 20 cash = 60 -> 0.30
    assert mf3.active_share({"A": 50.0, "B": 30.0}, index) == pytest.approx(0.30)
    assert mf3.active_share(index, index) == 0 and mf3.active_share({"X": 100.0}, index) == 1


def test_tracking_error_and_active_fee():
    # sample sd of +1%, -1%, +1%, -1% = sqrt(4 x 0.0001 / 3) = 0.011547; x sqrt(12) = 0.04
    assert mf3.tracking_error([0.01, -0.01, 0.01, -0.01]) == pytest.approx(0.04)
    assert mf3.active_fee(1.2, 0.30) == pytest.approx(0.04)  # 1.2% TER / 0.30 active share


# --- envelope -------------------------------------------------------------------------------------------

def test_closet_profile_flagged():
    fund_nav, bench = series(0.001)
    holdings = {"INE000000011": 42.0, "INE000000029": 38.0, "INE000000037": 18.0}  # 2% cash
    # |42-40| + |38-40| + |18-20| + 2 cash = 8 -> 0.04
    env = mf3.compute(SCHEME, holdings, SEP, INDEX, SEP, (1.05, date(2026, 9, 30)), fund_nav, bench, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert v["active_share"] == pytest.approx(0.04) and v["benchmark_weight_held"] == pytest.approx(0.98)
    assert v["tracking_error_36m"] < 0.04 and v["tracking_error_months"] == 36
    assert v["ter"] == pytest.approx(0.0105) and v["active_fee"] == pytest.approx(0.0105 / 0.04)
    assert any(f.severity == "watch" and "closet-indexer range" in f.reason for f in env.flags)
    assert env.gaps == [] and env.confidence.level == "medium"  # price-index caveat
    for f in env.flags:  # SPEC: describe, never "switch" or "exit"
        assert "switch" not in f.reason.lower() and "exit" not in f.reason.lower()


def test_active_fund_not_flagged():
    fund_nav, bench = series(0.001)
    holdings = {"INE000000045": 60.0, "INE000000011": 30.0}  # mostly outside the index
    env = mf3.compute(SCHEME, holdings, SEP, INDEX, SEP, None, fund_nav, bench, AS_OF, NOW)
    # |60-0| + |30-40| + |0-40| + |0-20| + 10 cash = 140 -> 0.70
    assert {m.id: m.value for m in env.metrics}["active_share"] == pytest.approx(0.70)
    assert env.flags == [] and [g.item for g in env.gaps] == ["expense ratio"]


def test_gaps():
    fund_nav, bench = series(0.001)
    h = {"INE000000011": 100.0}
    stale = mf3.compute(SCHEME, h, date(2026, 6, 1), INDEX, date(2026, 6, 1), None, fund_nav, bench, AS_OF, NOW)
    assert stale.confidence.level == "low" and "too old" in stale.gaps[0].reason
    mismatch = mf3.compute(SCHEME, h, SEP, INDEX, date(2026, 6, 1), None, fund_nav, bench, AS_OF, NOW)
    assert mismatch.gaps[0].item == "benchmark constituents"
    off = mf3.compute(SCHEME, h, SEP, {"INE000000011": 50.0}, SEP, None, fund_nav, bench, AS_OF, NOW)
    assert "sum to 50.0%" in off.gaps[0].reason
    idcw = mf3.compute({**SCHEME, "option": "IDCW"}, h, SEP, INDEX, SEP, None, fund_nav, bench, AS_OF, NOW)
    assert any(g.item == "tracking error" and "IDCW" in g.reason for g in idcw.gaps)
    index_fund = mf3.compute({**SCHEME, "category": "Other Scheme - Index Funds"}, h, SEP, INDEX, SEP, None,
                             fund_nav, bench, AS_OF, NOW)
    assert index_fund.gaps[0].item == "benchmark" and index_fund.confidence.level == "low"


# --- pipeline + run (DB) ----------------------------------------------------------------------------------

def test_disclosure_builders_validate():
    h = fund_disclosures.build_holdings([("100001", SEP, "INE000000011", 40, 1e7), ("100001", SEP, "INE000000011", 41, None),
                                         ("100001", date(2026, 9, 15), "INE000000011", 1, None), ("100001", SEP, "CASH", 5, None),
                                         ("100001", SEP, "INE000000029", 0, None), ("100001", SEP, "INE000000037", -2, None)])
    assert len(h.rows) == 2 and h.skipped == {"duplicate": 1, "bad_month": 1, "bad_isin": 1, "bad_weight": 1}
    c = fund_disclosures.build_constituents([("Nifty 100", SEP, "INE000000011", 101), ("", SEP, "INE000000011", 1)])
    assert c.rows == [] and c.skipped == {"bad_weight": 1, "bad_index": 1}
    t = fund_disclosures.build_expense_ratios([("100001", date(2026, 9, 30), 1.05), ("100001", date(2026, 9, 30), "x")])
    assert len(t.rows) == 1 and t.skipped == {"bad_ter": 1}


def test_run_end_to_end(db_conn):
    db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, option, scheme_type) VALUES"
                    " ('100001', 'T', 'AMC', 'Equity Scheme - Large Cap Fund', 'direct', 'Growth', 'open')")
    fund_nav, bench = series(0.001)
    for d, v in fund_nav:
        db_conn.execute("INSERT INTO fund_nav (amfi_code, nav_date, nav) VALUES ('100001', %s, %s)", (d, v))
    for d, v in bench:
        db_conn.execute("INSERT INTO index_prices (index_name, trade_date, close) VALUES ('Nifty 100', %s, %s)", (d, v))
    adapter = FakeAdapter(
        holdings={(2026, 9): [("100001", SEP, "INE000000011", 42, None), ("100001", SEP, "INE000000029", 38, None),
                              ("100001", SEP, "INE000000037", 18, None), ("999999", SEP, "INE000000011", 50, None)]},
        constituents={(2026, 9): [("Nifty 100", SEP, i, w) for i, w in INDEX.items()]},
        ters={(2026, 9): [("100001", date(2026, 9, 30), 1.05)]},
    )
    result = fund_disclosures.run_month(db_conn, 2026, 9, adapter)
    assert result["holdings"]["skipped"] == {"unknown_scheme": 1} and result["constituents"]["inserted"] == 3
    assert fund_disclosures.run_month(db_conn, 2026, 9, adapter)["holdings"]["inserted"] == 0  # idempotent
    assert fund_disclosures.run_month(db_conn, 2026, 8, adapter)["holdings"] == {"status": "no_data"}

    env = mf3.run(db_conn, mf3.MF3Input(amfi_code="100001", as_of=AS_OF))
    assert {m.id: m.value for m in env.metrics}["active_share"] == pytest.approx(0.04)
    assert all(s.providers == ["dev_local"] for s in env.sources)
    assert db_conn.execute("SELECT public_ok FROM agent_outputs").fetchone() == (False,)
