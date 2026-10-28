from datetime import date, datetime, time as dtime

import pytest

from services.agents.desks.markets import m6_peers as m6
from services.agents.envelope import Confidence, Envelope, Metric, Source
from services.agents.runlog import run_logged
from services.pipelines import classification
from services.pipelines.tests.fakes import FakeAdapter

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=m6.IST)


def cached(agent: str, subject: str, values: dict[str, float], day: date = AS_OF, providers=("dev_local",)) -> Envelope:
    """An M2/M7-shaped envelope carrying just the given metric values."""
    t = datetime.combine(day, dtime(15, 30), m6.IST)
    return Envelope(agent_id=agent, subject=subject, as_of=t, findings=[], flags=[], gaps=[],
                    metrics=[Metric(id=k, name=k, value=float(v), formula_id="f", source_query_id="s") for k, v in values.items()],
                    sources=[Source(id="s", kind="dataset", ref="test", retrieved_at=t, providers=list(providers))],
                    confidence=Confidence(level="high", reason="test"))


# ACME vs four peers. ROCE: peers .10 .15 .30 .20 -> 3 of 4 below .24; median (.15 + .20) / 2 = .175
# EBITDA margin: peers .10 .12 .15 (P4 has none) -> ACME .18 is the highest of 4 companies
# P/E (M7): peers 13 18 41 -> 2 of 3 below 27; median 18.   ROE: only 2 peers report it -> gap
M2 = {"ACME": {"roce": 0.24, "ebitda_margin": 0.18, "roe": 0.22},
      "P1": {"roce": 0.10, "ebitda_margin": 0.10, "roe": 0.12}, "P2": {"roce": 0.15, "ebitda_margin": 0.12, "roe": 0.30},
      "P3": {"roce": 0.30, "ebitda_margin": 0.15}, "P4": {"roce": 0.20}}
M7 = {"ACME": {"pe": 27}, "P1": {"pe": 13}, "P2": {"pe": 18}, "P3": {"pe": 41}}


def caches():
    return {"M2": {k: cached("M2", k, v) for k, v in M2.items()}, "M7": {k: cached("M7", k, v) for k, v in M7.items()}}


def test_percentile():
    assert m6.percentile(0.2, [0.1, 0.15, 0.25, 0.3]) == 0.5
    assert m6.percentile(0.2, [0.2, 0.1]) == 0.5  # ties do not count as lower
    assert m6.percentile(5, [6, 7]) == 0.0


def test_compute_ranks_against_industry():
    env = m6.compute("ACME", "INE000000011", ("industry", "Widgets"), caches(), AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert v["peer_group"] == "industry: Widgets"
    assert (v["roce"], v["roce_percentile"], v["roce_peer_median"], v["roce_peer_count"]) == (0.24, 0.75, pytest.approx(0.175), 4)
    assert v["ebitda_margin_percentile"] == 1.0 and v["ebitda_margin_peer_count"] == 3
    assert v["pe_percentile"] == pytest.approx(2 / 3) and v["pe_peer_median"] == 18
    assert "roe_percentile" not in v and any(g.item == "ROE rank" and "2 peers" in g.reason for g in env.gaps)
    assert any(f.reason == "EBITDA margin is the highest among 4 companies in the industry" for f in env.flags)
    assert env.confidence.level == "medium"
    for m in env.metrics:  # descriptive only
        assert not any(w in m.name.lower() for w in ("better", "worse", "cheap", "expensive"))


def test_unclassified_and_sector_fallback():
    none = m6.compute("ACME", "INE000000011", None, {}, AS_OF, NOW)
    assert none.confidence.level == "low" and none.gaps[0].item == "peer group"
    sector = m6.compute("ACME", "INE000000011", ("sector", "Industrials"), caches(), AS_OF, NOW)
    assert {m.id: m.value for m in sector.metrics}["peer_group"] == "sector: Industrials"
    assert any(g.item == "industry peers" for g in sector.gaps)


def test_classification_build():
    p = classification.build([("INE000000011", "Industrials", "Widgets"), ("INE000000011", "X", "Y"),
                              ("BAD", "A", "B"), ("INE000000029", "Industrials", " ")])
    assert p.rows == [("INE000000011", "Industrials", "Widgets")]
    assert p.skipped == {"duplicate": 1, "bad_isin": 1, "missing_sector_or_industry": 1}


def test_run_reads_caches_and_inherits_providers(db_conn):
    isins = {k: f"INE00000{i:04d}" for i, k in enumerate(M2)}
    for k, isin in isins.items():
        db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, last_seen) VALUES (%s, %s, %s, %s)", (isin, k, k, AS_OF))
    adapter = FakeAdapter(sectors=[(i, "Industrials", "Widgets") for i in isins.values()] + [("INE999999999", "X", "Y")])
    assert classification.run(db_conn, adapter) == {"status": "loaded", "securities_updated": 5, "skipped": {"unknown_security": 1}}
    assert classification.run(db_conn, adapter)["securities_updated"] == 0  # idempotent

    def log(env, ok=True):
        def go():
            if not ok:
                raise RuntimeError("boom")
            return env, {"s": env.sources[0].providers}
        try:
            run_logged(db_conn, env.agent_id, env.subject, env.as_of, go)
        except RuntimeError:
            pass

    for k, vals in M2.items():
        log(cached("M2", k, vals))
    for k, vals in M7.items():
        log(cached("M7", k, vals))
    log(cached("M7", "P1", {"pe": 999}, day=date(2026, 10, 22)))  # logged later but older data: must not win
    log(cached("M7", "P2", {"pe": 1}), ok=False)  # failed runs are never read
    log(cached("M2", "P3", {"roce": 5.0}, day=date(2026, 11, 5)))  # after as_of: ignored

    env = m6.run(db_conn, m6.M6Input(subject="ACME", as_of=AS_OF))
    v = {m.id: m.value for m in env.metrics}
    assert v["roce_percentile"] == 0.75 and v["pe_peer_median"] == 18
    assert {s.id: s.providers for s in env.sources} == {"classification": ["dev_local"], "peer_m2": ["dev_local"], "peer_m7": ["dev_local"]}
    assert db_conn.execute("SELECT o.public_ok FROM agent_outputs o JOIN agent_runs r ON r.id = o.run_id WHERE r.agent_id = 'M6'").fetchone() == (False,)


def test_stale_valuation_cache_is_ignored(db_conn):
    db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, sector, industry, classification_source) VALUES"
                    " ('INE000000011', 'ACME', 'A', 'I', 'W', 'dev_local'), ('INE000000029', 'P1', 'P', 'I', 'W', 'dev_local'),"
                    " ('INE000000037', 'P2', 'P', 'I', 'W', 'dev_local'), ('INE000000045', 'P3', 'P', 'I', 'W', 'dev_local')")
    for k, pe in (("ACME", 27), ("P1", 13), ("P2", 18), ("P3", 41)):
        run_logged(db_conn, "M7", k, NOW, lambda k=k, pe=pe: (cached("M7", k, {"pe": pe}, day=date(2026, 10, 10)), {"s": ["dev_local"]}))
    _, _, _, caches_, _ = m6.load_inputs(db_conn, "ACME", AS_OF)
    assert caches_["M7"] == {}  # all 17 days old
