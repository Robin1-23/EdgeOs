from datetime import date, datetime

import pytest

from services.agents.desks.markets import m3_forensics as m3
from services.agents.desks.markets.tests.test_m2_fundamentals import ISIN, acme_records, rows_of
from services.pipelines import fundamentals, governance
from services.pipelines.tests.fakes import FakeAdapter

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=m3.IST)
# Latest quarter: promoter 54%, pledge 22% (>= 20 -> triggered); a year earlier pledge 10% (+12pp -> triggered);
# 3 years earlier promoter 60% (-6pp -> triggered)
SHAREHOLDING = [(date(2023, 9, 30), 60.0, 5.0), (date(2025, 9, 30), 56.0, 10.0), (date(2026, 9, 30), 54.0, 22.0)]
EVENTS = [(date(2025, 2, 10), "auditor_resignation", "statutory auditor"), (date(2022, 1, 5), "auditor_resignation", "too old")]


def financials():
    """ACME (cash conversion 0.83 -> clear) + contingent 60 vs equity 100 (0.6 -> triggered) + shares
    FY23..FY26 2, 2, 4 (1:1 bonus, excluded), 4.5 -> dilution 12.5% (triggered)."""
    recs = acme_records() + [(ISIN, date(2026, 3, 31), "A", True, "contingent_liabilities", 60)]
    recs += [(ISIN, date(y, 3, 31), "A", True, "shares_outstanding", s) for y, s in ((2023, 2), (2024, 2), (2025, 4), (2026, 4.5))]
    return rows_of(recs)


# --- formulas, hand-worked ------------------------------------------------------------------------------

def test_share_dilution_skips_split_bonus_steps():
    # 100 -> 102 (x1.02), -> 204 (x2.0, bonus: excluded), -> 210 (x1.0294); 1.02 x 1.0294 - 1 = 5%
    assert m3.share_dilution([100, 102, 204, 210]) == (pytest.approx(0.05), 1)
    assert m3.share_dilution([10, 11, 12.1]) == (pytest.approx(0.21), 0)  # two 10% raises


def test_red_flag_score():
    assert m3.red_flag_score({"pledge_level": True, "cash_gap": False, "dilution": True}) == (25.0, 45.0)
    assert m3.red_flag_score({}) == (0.0, 0.0)
    assert sum(w for w, _ in m3.CHECKS.values()) == 100


# --- envelope -------------------------------------------------------------------------------------------

def test_compute_all_checks():
    env = m3.compute(ISIN, "ACME", financials(), SHAREHOLDING, EVENTS, date(2026, 10, 20), AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert (v["promoter_pledged_pct"], v["pledge_change_1y"], v["promoter_change_3y"]) == (22.0, 12.0, -6.0)
    assert v["auditor_resignations_3y"] == 1  # the 2022 one is outside 3 years
    assert v["cash_conversion_3y"] == pytest.approx(0.834783, abs=1e-6) and v["check_cash_gap"] == "clear"
    assert v["contingent_to_networth"] == pytest.approx(0.6)
    assert v["dilution_3y"] == pytest.approx(0.125) and v["split_bonus_steps"] == 1
    # triggered: pledge_level 15 + pledge_rise 15 + promoter_stake_fall 10 + auditor 20 + contingent 10 + dilution 10
    assert (v["red_flag_score"], v["evaluated_weight"]) == (80.0, 100.0)
    severities = {f.reason: f.severity for f in env.flags}
    assert severities[m3.CHECKS["auditor_resignation"][1]] == "alert" and len(env.flags) == 6
    assert [g.item for g in env.gaps] == ["related-party transactions"]
    assert env.confidence.level == "medium"


def test_missing_coverage_is_a_gap_not_a_clean_record():
    env = m3.compute(ISIN, "ACME", financials(), SHAREHOLDING, [], None, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert "check_auditor_resignation" not in v and v["evaluated_weight"] == 80.0
    assert any(g.item == "auditor_resignation" for g in env.gaps)
    covered = m3.compute(ISIN, "ACME", financials(), SHAREHOLDING, [], date(2026, 10, 20), AS_OF, NOW)
    assert {m.id: m.value for m in covered.metrics}["check_auditor_resignation"] == "clear"


def test_no_data_low_confidence_and_stale_shareholding():
    env = m3.compute(ISIN, "ACME", [], [(date(2025, 12, 31), 50.0, 30.0)], [], None, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert v["evaluated_weight"] == 0 and v["red_flag_score"] == 0 and env.confidence.level == "low"
    assert {"pledge_level", "cash_gap", "dilution"} <= {g.item for g in env.gaps}  # 300-day-old quarter is stale


# --- pipeline + run (DB) ----------------------------------------------------------------------------------

def test_governance_builders_validate():
    sh = governance.build_shareholding([(ISIN, date(2026, 9, 30), 54, 22), (ISIN, date(2026, 9, 30), 54, 22),
                                        (ISIN, date(2026, 6, 30), 101, 0), ("BAD", date(2026, 6, 30), 1, 1)])
    assert len(sh.rows) == 1 and sh.skipped == {"duplicate": 1, "bad_pct": 1, "bad_key": 1}
    ev = governance.build_events([(ISIN, date(2025, 2, 10), "auditor_resignation", "x"), (ISIN, date(2025, 2, 10), "ceo_exit", "")],
                                 date(2026, 10, 20))
    assert len(ev.rows) == 1 and ev.skipped == {"unknown_kind": 1} and ev.covered_until == date(2026, 10, 20)


def test_run_end_to_end(db_conn):
    db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, last_seen) VALUES (%s, 'ACME', 'Acme', %s)", (ISIN, AS_OF))
    fundamentals.load(db_conn, fundamentals.build([(ISIN, *r) for r in financials()]))
    adapter = FakeAdapter(shareholding={ISIN: [(ISIN, *r) for r in SHAREHOLDING]},
                          events={ISIN: ([(ISIN, *e) for e in EVENTS], date(2026, 10, 20))})
    result = governance.run_security(db_conn, ISIN, adapter)
    assert result["shareholding"]["inserted"] == 3 and result["events"]["inserted"] == 2
    assert governance.run_security(db_conn, ISIN, adapter)["events"]["inserted"] == 0  # idempotent
    older = FakeAdapter(events={ISIN: ([], date(2026, 1, 1))})
    governance.run_security(db_conn, ISIN, older)  # coverage never moves backwards
    assert db_conn.execute("SELECT covered_until FROM corporate_events_coverage").fetchone() == (date(2026, 10, 20),)

    env = m3.run(db_conn, m3.M3Input(subject="ACME", as_of=AS_OF))
    v = {m.id: m.value for m in env.metrics}
    assert (v["red_flag_score"], v["evaluated_weight"]) == (80.0, 100.0)
    assert all(s.providers == ["dev_local"] for s in env.sources)
    assert db_conn.execute("SELECT public_ok FROM agent_outputs").fetchone() == (False,)
