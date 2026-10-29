from datetime import date, datetime

import pytest

from services.agents.desks.markets import m5_news as m5
from services.agents.desks.markets.tests import m5_fixtures as fx
from services.agents.llm import client
from services.agents.llm.client import RecordedProvider
from services.agents.mother.compliance import check
from services.pipelines import news
from services.pipelines.tests.fakes import FakeAdapter

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=m5.IST)


@pytest.fixture
def m5_recordings(monkeypatch):
    monkeypatch.setattr(client, "_provider", RecordedProvider(fx.RECORDINGS))


def matched():
    """What M5.load_inputs returns: headlines naming ACME within the 60-day window."""
    names, symbols = m5.aliases_for(fx.NAME, fx.SYMBOL, [])
    start = datetime(2026, 10, 28, tzinfo=m5.IST) - m5.timedelta(days=2 * m5.WINDOW_DAYS)
    return [{"id": i, "published_at": n[0], "publisher": n[1], "headline": n[2], "url": n[3]}
            for i, n in enumerate(fx.NEWS, 1) if m5.mentions(n[2], names, symbols) and n[0] >= start]


def all_labels():
    return {h["id"]: fx.LABELS[h["headline"]] for h in matched()}


# --- matching and maths, hand-worked --------------------------------------------------------------------

def test_aliases_and_matching():
    names, symbols = m5.aliases_for("Acme Widgets Limited", "ACME", ["Acme"])
    assert names == ["Acme Widgets", "Acme"] and symbols == ["ACME"]
    names, symbols = m5.aliases_for("Acme Widgets Limited", "ACME", [])
    assert m5.mentions("Auditor quits Acme Widgets citing other assignments", names, symbols)
    assert m5.mentions("ACME shares slip 3%", names, symbols)
    assert not m5.mentions("Acme Corp of US reports earnings", names, symbols)  # different company
    assert not m5.mentions("acme rally lifts small caps", names, symbols)  # symbols are case-sensitive
    assert not m5.mentions("ACMEX launches new fund", names, symbols)  # whole words only
    assert m5.aliases_for("Bharat Electronics Ltd.", "BEL", [])[0] == ["Bharat Electronics"]
    assert m5.aliases_for(None, "IT", []) == ([], [])  # too short to match safely


def test_net_sentiment_and_coverage_change():
    # 4 positive, 3 negative, 1 neutral -> (4 - 3) / 8
    assert m5.net_sentiment(["positive"] * 4 + ["negative"] * 3 + ["neutral"]) == 0.125
    assert m5.coverage_change(8, 2) == 3.0 and m5.coverage_change(3, 0) is None


# --- envelope (pure) ------------------------------------------------------------------------------------

def test_compute_counts_labels_and_headlines():
    env = m5.compute(fx.ISIN, fx.SYMBOL, matched(), all_labels(), None, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    # 10 matched; last 30 days (from 28 Sep): ids 1-8; previous 30 days: 9, 10; last 7 days (from 21 Oct): 1-6
    assert (v["headlines_7d"], v["headlines_30d"], v["headlines_prev_30d"]) == (6, 8, 2)
    assert v["publishers_30d"] == 4 and v["coverage_change_30d"] == 3.0  # 8 / 2 - 1
    assert v["net_sentiment_30d"] == 0.125 and (v["positive_30d"], v["negative_30d"], v["neutral_30d"]) == (4, 3, 1)
    assert v["news_4"] == "negative; governance"
    assert env.findings[0].text == "Daily Ledger, 27 Oct 2026: “Acme Widgets Q2 profit rises to Rs 22 crore”"
    assert len(env.findings) == 8 and {s.ref for s in env.sources if s.kind == "api"} == {f"https://example.invalid/n/{i}" for i in range(1, 9)}
    # 6 in 7 days vs a prior weekly rate of 2 / (30/7) = 0.47 -> spike
    assert any(f.reason.startswith("6 headlines in 7 days") for f in env.flags)
    assert not any(f.severity == "watch" for f in env.flags)  # net +0.125 is not a negative skew
    assert env.gaps == [] and env.confidence.level == "high"


def test_negative_skew_and_label_gaps():
    labels = {i: ("negative", "other") for i in all_labels()}
    env = m5.compute(fx.ISIN, fx.SYMBOL, matched(), labels, None, AS_OF, NOW)
    assert any(f.severity == "watch" and "8 of 8" in f.reason for f in env.flags)
    partial = {i: lab for i, lab in all_labels().items() if i <= 5}
    env = m5.compute(fx.ISIN, fx.SYMBOL, matched(), partial, "openai 500", AS_OF, NOW)
    reasons = [g.reason for g in env.gaps]
    assert "labelling failed: openai 500" in reasons and "3 of 8 recent headlines not labelled" in reasons
    assert {m.id: m.value for m in env.metrics}["news_7"] == "unlabelled" and env.confidence.level == "medium"


def test_no_coverage():
    env = m5.compute(fx.ISIN, fx.SYMBOL, [], {}, None, AS_OF, NOW)
    assert env.findings == [] and env.confidence.level == "low"
    assert {g.item for g in env.gaps} == {"coverage change", "headlines"}


def test_broker_calls_in_headlines_are_left_for_the_compliance_guard():
    env = m5.compute(fx.ISIN, fx.SYMBOL, matched(), all_labels(), None, AS_OF, NOW)
    flagged = [f.text for f in env.findings if check(f.text)]
    assert flagged == ["Street Notes, 21 Oct 2026: “Brokerage upgrades Acme Widgets to buy”"]


# --- pipeline + run with recorded labels (DB) -------------------------------------------------------------

def test_news_build_validates():
    p = news.build(fx.RECORDS + [(fx.NEWS[0][0], "X", "Dup", fx.NEWS[0][3]), (datetime(2026, 10, 1), "X", "Naive", "https://e.invalid/a"),
                                 (fx.NEWS[0][0], "", "No publisher", "https://e.invalid/b"), (fx.NEWS[0][0], "X", "http", "http://e.invalid/c")])
    assert len(p.rows) == 14 and p.skipped == {"duplicate": 1, "bad_published_at": 1, "no_publisher": 1, "bad_url": 1}


def test_run_with_recording_and_cache(db_conn, m5_recordings):
    db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, last_seen) VALUES (%s, %s, %s, %s)", (fx.ISIN, fx.SYMBOL, fx.NAME, AS_OF))
    assert news.run(db_conn, date(2026, 8, 1), FakeAdapter(news_records=fx.RECORDS))["inserted"] == 14

    env = m5.run(db_conn, m5.M5Input(subject=fx.SYMBOL, as_of=AS_OF))
    v = {m.id: m.value for m in env.metrics}
    assert (v["headlines_30d"], v["net_sentiment_30d"]) == (8, 0.125)
    assert db_conn.execute("SELECT count(*) FROM news_labels").fetchone() == (8,)  # out-of-range index 99 ignored
    assert db_conn.execute("SELECT count(*) FROM llm_calls WHERE status = 'ok'").fetchone() == (1,)

    again = m5.run(db_conn, m5.M5Input(subject=fx.SYMBOL, as_of=AS_OF))
    assert {m.id: m.value for m in again.metrics} == v
    assert db_conn.execute("SELECT count(*) FROM llm_calls").fetchone() == (1,)  # all labels came from the cache
    assert all(s.providers == ["dev_local"] for s in env.sources)
    assert db_conn.execute("SELECT public_ok FROM agent_outputs ORDER BY run_id DESC LIMIT 1").fetchone() == (False,)
