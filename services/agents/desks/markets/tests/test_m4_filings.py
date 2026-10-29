from datetime import date, datetime

import pytest

from services.agents.desks.markets import m4_filings as m4
from services.agents.desks.markets.tests import m4_fixtures as fx
from services.agents.llm import client
from services.agents.llm.client import RecordedProvider
from services.agents.mother.compliance import check
from services.pipelines import filings
from services.pipelines.tests.fakes import FakeAdapter

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=m4.IST)


@pytest.fixture
def m4_recordings(monkeypatch):
    monkeypatch.setattr(client, "_provider", RecordedProvider(fx.RECORDINGS))


def summaries():
    return {f["id"]: m4.FilingSummary.model_validate(fx.RESPONSES[f["title"]]) for f in fx.filing_dicts() if f["text"]}


# --- verification ---------------------------------------------------------------------------------------

def test_check_statement():
    text = fx.RESULTS_TEXT
    ok = m4.Statement(text="Profit after tax was Rs 22 crore.", quote="Profit after  tax was\nRs 22 crore.")
    assert m4.check_statement(ok, text) is None  # whitespace differences are fine
    curly = m4.Statement(text="The MD expects margins to improve.", quote="“We are pleased with the steady demand")
    assert m4.check_statement(curly, text) is None  # curly quotes folded
    invented = m4.Statement(text="Sales hit a record.", quote="Sales hit an all-time record")
    assert m4.check_statement(invented, text) == "quote not found in the filing"
    pct = m4.Statement(text="Profit rose 22% year on year.", quote="Profit after tax was Rs 22 crore.")
    assert m4.check_statement(pct, text) == "numbers not in the quote: 22%"  # 22% is not 22 crore
    short = m4.Statement(text="Revenue.", quote="Revenue")
    assert m4.check_statement(short, text) == "quote not found in the filing"


def test_numbers():
    assert m4.numbers("Rs 1,234.50 crore, up 12.5 per cent and 3%.") == {"1234.50", "12.5%", "3%"}


# --- envelope (pure) ------------------------------------------------------------------------------------

def test_compute_restates_only_verified_statements():
    env = m4.compute(fx.ISIN, fx.SYMBOL, fx.filing_dicts(), summaries(), AS_OF, NOW)
    texts = [f.text for f in env.findings]
    assert len(texts) == 8  # results 3 summary + 2 guidance, auditor 1, concall 1 summary + 1 guidance
    assert "Profit after tax rose 25% from a year earlier." not in texts
    assert "The CEO said sales reached a record." not in texts
    assert "Management guidance: The company plans capital expenditure of Rs 40 crore in FY27." in texts
    gaps = {g.item: g.reason for g in env.gaps}
    assert gaps["Newspaper advertisement (01 Oct 2026)"] == "filing text not available"
    assert gaps["Financial results for Q2 FY27 (20 Oct 2026)"] == "1 statement(s) dropped: numbers not in the quote: 25%"
    assert gaps["Q1 FY27 earnings call transcript (05 Aug 2026)"] == "1 statement(s) dropped: quote not found in the filing"
    v = {m.id: m.value for m in env.metrics}
    assert v["event_3"] == "auditor_change" and v["tone_1"] == "positive" and v["tone_4"] == "negative"
    assert v["tone_change"] == "negative -> positive"
    sev = {f.reason: f.severity for f in env.flags}
    assert sev["filing tagged auditor change: Resignation of statutory auditor (12 Sep 2026)"] == "watch"
    assert sev["management tone changed from negative to positive"] == "info"
    assert {s.ref for s in env.sources} == {f[5] for f in fx.FILINGS if f[6]}  # every source is the filing's URL
    assert all(s.kind == "filing" for s in env.sources)
    assert env.confidence.level == "medium"


def test_restatements_pass_the_compliance_rules():
    env = m4.compute(fx.ISIN, fx.SYMBOL, fx.filing_dicts(), summaries(), AS_OF, NOW)
    assert [f.text for f in env.findings if check(f.text)] == []


def test_tone_needs_evidence_and_summary_errors_become_gaps():
    s = summaries()
    s[1] = s[1].model_copy(update={"tone_evidence": ["We are thrilled beyond words"]})  # not in the filing
    s[4] = "results summary failed: openai 500"
    env = m4.compute(fx.ISIN, fx.SYMBOL, fx.filing_dicts(), s, AS_OF, NOW)
    gaps = [(g.item, g.reason) for g in env.gaps]
    assert ("Financial results for Q2 FY27 (20 Oct 2026)", "tone not shown: no evidence quote found in the filing") in gaps
    assert ("Q1 FY27 earnings call transcript (05 Aug 2026)", "summary not produced: results summary failed: openai 500") in gaps
    assert "tone_change" not in {m.id for m in env.metrics}  # no two verified tones left


def test_no_filings():
    env = m4.compute(fx.ISIN, fx.SYMBOL, [], {}, AS_OF, NOW)
    assert env.findings == [] and env.confidence.level == "low" and env.gaps[0].item == "filings"


# --- pipeline + run with recorded responses (DB) ----------------------------------------------------------

def test_filings_build_validates():
    recs = fx.FILINGS + [(fx.ISIN, "NSE", fx.FILINGS[0][2], "results", "dup", fx.FILINGS[0][5], "x"),
                         (fx.ISIN, "MCX", fx.FILINGS[0][2], "results", "t", "https://e.invalid/a", None),
                         (fx.ISIN, "NSE", datetime(2026, 10, 1), "results", "t", "https://e.invalid/b", None),
                         (fx.ISIN, "NSE", fx.FILINGS[0][2], "rumour", "t", "https://e.invalid/c", None)]
    p = filings.build(recs)
    assert len(p.rows) == 4 and p.skipped == {"duplicate": 1, "bad_exchange": 1, "bad_filed_at": 1, "unknown_category": 1}


def test_run_with_recordings_and_cache(db_conn, m4_recordings):
    db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, last_seen) VALUES (%s, %s, 'Acme', %s)", (fx.ISIN, fx.SYMBOL, AS_OF))
    assert filings.run(db_conn, date(2026, 7, 1), FakeAdapter(filing_records=fx.FILINGS))["inserted"] == 4

    env = m4.run(db_conn, m4.M4Input(subject=fx.SYMBOL, as_of=AS_OF))
    assert len(env.findings) == 8 and all(s.providers == ["dev_local"] for s in env.sources)
    calls = db_conn.execute("SELECT count(*), sum(cost_usd) FROM llm_calls WHERE status = 'ok'").fetchone()
    (run_cost,) = db_conn.execute("SELECT cost_usd FROM agent_runs WHERE agent_id = 'M4'").fetchone()
    assert calls[0] == 3 and run_cost == calls[1] and run_cost > 0  # one call per filing with text

    again = m4.run(db_conn, m4.M4Input(subject=fx.SYMBOL, as_of=AS_OF))
    assert [f.text for f in again.findings] == [f.text for f in env.findings]
    assert db_conn.execute("SELECT count(*) FROM llm_calls").fetchone() == (3,)  # second run served from the cache
    assert db_conn.execute("SELECT public_ok FROM agent_outputs ORDER BY run_id DESC LIMIT 1").fetchone() == (False,)


def test_missing_recording_is_a_gap_not_a_guess(db_conn):
    # default test provider: recordings dir without M4 responses
    db_conn.execute("INSERT INTO securities (isin, nse_symbol, name) VALUES (%s, %s, 'Acme')", (fx.ISIN, fx.SYMBOL))
    filings.run(db_conn, date(2026, 7, 1), FakeAdapter(filing_records=fx.FILINGS))
    env = m4.run(db_conn, m4.M4Input(subject=fx.SYMBOL, as_of=AS_OF))
    assert env.findings == [] and env.confidence.level == "low"
    assert sum("summary not produced: " in g.reason and "no recorded response" in g.reason for g in env.gaps) == 3
