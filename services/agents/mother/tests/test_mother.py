import contextlib
import json
import time
from datetime import date, datetime, time as dtime

import pytest

from services.agents import registry
from services.agents.envelope import Confidence, Envelope, Finding, Flag, Gap, Metric, Source
from services.agents.llm import client
from services.agents.llm.client import LLMError, RawResponse
from services.agents.mother import debate, judge, orchestrator, planner, verifier
from services.agents.mother.compliance import DISCLAIMER
from services.agents.runlog import run_logged

AS_OF = date(2026, 10, 27)
IST = registry.IST
T = datetime(2026, 10, 27, 15, 30, tzinfo=IST)


# --- helpers --------------------------------------------------------------------------------------------

def envelope(agent, subject, metrics, findings=(), flags=(), level="high", gaps=(), url="https://example.invalid/data"):
    """metrics: {id: (name, value, unit)}; findings: [(id, text, [metric ids])]; flags: [(severity, reason, [ids])]."""
    return Envelope(
        agent_id=agent, subject=subject, as_of=T,
        metrics=[Metric(id=k, name=n, value=v, unit=u, formula_id="f", source_query_id="src") for k, (n, v, u) in metrics.items()],
        findings=[Finding(id=i, text=t, metric_ids=m) for i, t, m in findings],
        flags=[Flag(severity=s, reason=r, metric_ids=m) for s, r, m in flags],
        sources=[Source(id="src", kind="dataset", ref=url, retrieved_at=T)],
        confidence=Confidence(level=level, reason="test"), gaps=[Gap(item=g, reason="missing") for g in gaps])


ENVS = {
    "M2": lambda s: envelope("M2", s, {"roce": ("ROCE, FY26", 0.237, "ratio"), "revenue": ("Revenue, FY26", 206.0, "INR cr")},
                             [("growth", "Revenue grew for five straight years.", ["revenue"])]),
    "M3": lambda s: envelope("M3", s, {"red_flag_score": ("Red-flag score", 20.0, "points")},
                             flags=[("alert", "an auditor resigned in the last 3 years", ["red_flag_score"])], level="medium"),
    "M5": lambda s: envelope("M5", s, {"news_6": ("Headline tone and type", "positive; rating_or_broker_action", None)},
                             [("headline_6", "Street Notes, 21 Oct 2026: “Brokerage upgrades Acme Widgets to buy”", ["news_6"])]),
    "M7": lambda s: envelope("M7", s, {"pe": ("P/E (TTM profit)", 27.27, "x")}),
    "M6": lambda s: envelope("M6", s, {"roce_percentile": ("Share of peers with a lower ROCE", 0.75, "ratio")}, gaps=["ROE rank"]),
}


@pytest.fixture
def fake_agents(monkeypatch):
    """Stand-in specialists with known envelopes; .runs counts real (non-cached) runs, .tamper changes recompute."""
    state = {"runs": [], "tamper": {}, "fail": set(), "slow": set()}

    def spec(agent, deps=()):
        def run(conn, subject, as_of):
            state["runs"].append(agent)
            if agent in state["slow"]:
                time.sleep(1.5)
            if agent in state["fail"]:
                raise RuntimeError("source table empty")
            return run_logged(conn, agent, subject, datetime.combine(as_of, dtime(0), IST),
                              lambda: (ENVS[agent](subject), {"src": ["dev_local"]}))

        def recompute(conn, subject, as_of):
            env = ENVS[agent](subject)
            if agent in state["tamper"]:
                mid, value = state["tamper"][agent]
                env = env.model_copy(update={"metrics": [m.model_copy(update={"value": value}) if m.id == mid else m for m in env.metrics]})
            return env
        return registry.Spec(agent, "stock", 1, 1 if agent in ("M5",) else 10, deps, run, recompute)

    monkeypatch.setattr(registry, "AGENTS", {a: spec(a, ("M2", "M7") if a == "M6" else ()) for a in ENVS})
    monkeypatch.setattr(registry, "PAGE_AGENTS", {"stock": ["M2", "M3", "M5", "M7", "M6"], "fund": []})
    return state


class Scripted:
    """LLM stand-in answering by schema name; records (schema, prompt). Values may be dicts or callables(prompt)."""

    name = "scripted"

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def complete_json(self, model, system, user, schema_name, schema, effort):
        self.calls.append((schema_name, user))
        reply = self.replies[schema_name]
        reply = reply(user) if callable(reply) else reply
        if isinstance(reply, Exception):
            raise reply
        return RawResponse(text=json.dumps(reply), model=model, input_tokens=1000, output_tokens=200)


DEBATE = {
    "bull_round1": {"points": [{"text": "Returns on capital are strong.", "cites": ["M2:roce"]},
                               {"text": "Margins were 18 percent.", "cites": ["M2:roce"]},  # digits: dropped
                               {"text": "Growth is broad based.", "cites": ["M9:nothing"]}]},  # no real cite: dropped
    "bear_round1": {"points": [{"text": "An auditor resignation is a governance concern.", "cites": ["M3:flag:0"]}]},
    "bull_round2": {"points": [{"text": "The company is set to outperform the market next year.", "cites": ["M2:roce"]}]},  # compliance
    "bear_round2": {"points": [{"text": "Valuation sits above most peers.", "cites": ["M7:pe", "M6:roce_percentile"]}]},
    "Synthesis": {"summary": [{"text": "The company earns high returns on capital and has faced an auditor exit.",
                               "cites": ["M2:roce", "M3:flag:0"]}],
                  "key_items": ["M2:roce", "M5:finding:headline_6", "M2:invented"],
                  "risk_notes": [{"text": "Governance questions remain open after the auditor exit.", "cites": ["M3:flag:0"]}]},
    "LLMVerdict": {"flagged": [], "reasons": []},
}


@pytest.fixture
def llm(monkeypatch):
    provider = Scripted(dict(DEBATE))
    monkeypatch.setattr(client, "_provider", provider)
    return provider


@pytest.fixture
def acme(db_conn):
    db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, last_seen) VALUES ('INE000000011', 'ACME', 'Acme Widgets Limited', %s)", (AS_OF,))
    db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, option, scheme_type) VALUES"
                    " ('100001', 'Alpha Flexi Cap', 'AMC', 'Equity Scheme - Flexi Cap Fund', 'direct', 'Growth', 'open')")
    return db_conn


# --- planner --------------------------------------------------------------------------------------------

def test_resolve_subjects(acme):
    assert planner.resolve_stock(acme, "acme") == "ACME" and planner.resolve_stock(acme, "INE000000011") == "ACME"
    assert planner.resolve_stock(acme, "Acme Widgets Ltd") == "ACME" and planner.resolve_stock(acme, "Acme Widg") is None
    assert planner.resolve_fund(acme, "100001") == "100001" and planner.resolve_fund(acme, "alpha flexi cap") == "100001"
    assert planner.resolve_fund(acme, "999999") is None


def test_dependencies_and_cache_freshness(acme, fake_agents):
    assert planner.with_dependencies(["M6"]) == ["M2", "M7", "M6"]
    for day in (date(2026, 10, 20), date(2026, 10, 27)):
        run_logged(acme, "M2", "ACME", datetime.combine(day, dtime(0), IST), lambda: (ENVS["M2"]("ACME"), {"src": ["dev_local"]}))
    latest = acme.execute("SELECT max(id) FROM agent_runs WHERE agent_id = 'M2'").fetchone()[0]
    assert planner.cached_run(acme, "M2", "ACME", AS_OF, 1) == latest
    assert planner.cached_run(acme, "M2", "ACME", date(2026, 10, 25), 1) is None  # the 20 Oct run is 5 days old
    plan = planner.page_plan(acme, "stock", "acme", AS_OF)
    assert [t.agent_id for t in plan.tasks] == ["M2", "M3", "M5", "M7", "M6"] and plan.tasks[0].cached_run_id == latest


def test_ask_plan(acme, fake_agents, monkeypatch):
    def plan_with(reply):
        monkeypatch.setattr(client, "_provider", Scripted({"AskIntent": reply}))
        return planner.ask_plan(acme, "question", AS_OF)

    p = plan_with({"intent": "stock_research", "subjects": ["Acme Widgets"], "agents": ["M6", "MF2"]})
    assert p.status == "ready" and p.subjects == ["ACME"] and [t.agent_id for t in p.tasks] == ["M2", "M7", "M6"]  # MF2 dropped
    assert [t.agent_id for t in plan_with({"intent": "stock_research", "subjects": ["ACME"], "agents": []}).tasks] == \
        registry.PAGE_AGENTS["stock"]
    nope = plan_with({"intent": "stock_research", "subjects": ["Imaginary Corp"], "agents": []})
    assert nope.status == "needs_clarification" and "Imaginary Corp" in nope.message
    assert plan_with({"intent": "unsupported", "subjects": [], "agents": []}).message == planner.UNSUPPORTED_MESSAGE
    assert plan_with(LLMError("openai 500")).status == "needs_clarification"


def test_dispatch_order_cache_failures_and_timeouts(acme, fake_agents):
    run_logged(acme, "M3", "ACME", T, lambda: (ENVS["M3"]("ACME"), {"src": ["dev_local"]}))
    fake_agents["fail"].add("M5")
    plan = planner.page_plan(acme, "stock", "ACME", AS_OF)
    out = planner.dispatch(plan, acme, AS_OF)
    status = {a: o.status for (a, _), o in out.items()}
    assert status == {"M2": "ok", "M3": "cached", "M5": "failed", "M7": "ok", "M6": "ok"}
    assert fake_agents["runs"] == ["M2", "M5", "M7", "M6"]  # M3 from cache; M6 waits for M2 and M7
    assert out[("M5", "ACME")].error == "RuntimeError: source table empty"

    fake_agents["runs"].clear()
    fake_agents["fail"].clear()
    fake_agents["slow"].add("M5")  # budget 1 s, takes 1.5 s
    plan = planner.Plan(status="ready", kind="stock", subjects=["ACME"],
                        tasks=[planner.Task("M5", "ACME", 1, ()), planner.Task("M7", "ACME", 10, ())])
    out = planner.dispatch(plan, acme, AS_OF, connect=lambda: contextlib.nullcontext(acme))
    assert out[("M5", "ACME")].status == "timed_out" and out[("M7", "ACME")].status == "ok"


# --- verifier -------------------------------------------------------------------------------------------

def test_verifier_drops_mismatches_and_what_cites_them(acme, fake_agents):
    env = ENVS["M2"]("ACME")
    same, checks = verifier.verify(acme, "M2", "ACME", AS_OF, env)
    assert same == env and not any(c.dropped for c in checks)
    fake_agents["tamper"]["M2"] = ("revenue", 210.0)  # data changed since the cached run
    v, checks = verifier.verify(acme, "M2", "ACME", AS_OF, env)
    assert [m.id for m in v.metrics] == ["roce"] and v.findings == []  # the growth finding cited revenue
    bad = next(c for c in checks if c.dropped)
    assert (bad.metric_id, bad.reported, bad.recomputed, bad.reason) == ("revenue", "206.0", "210.0", "mismatch beyond tolerance")
    fake_agents["tamper"]["M3"] = ("red_flag_score", 20.0000000001)  # float noise is not a mismatch
    v, _ = verifier.verify(acme, "M3", "ACME", AS_OF, ENVS["M3"]("ACME"))
    assert len(v.flags) == 1


def test_verifier_handles_unreproducible_and_failed_recompute(acme, monkeypatch, fake_agents):
    env = ENVS["M2"]("ACME").model_copy(update={"metrics": ENVS["M2"]("ACME").metrics + [
        Metric(id="extra", name="Extra", value=1.0, formula_id="f", source_query_id="src")]})
    v, checks = verifier.verify(acme, "M2", "ACME", AS_OF, env)
    assert "extra" not in {m.id for m in v.metrics} and any(c.reason == "not reproducible from current data" for c in checks)

    def boom(conn, s, a):
        raise ValueError("db gone")
    monkeypatch.setitem(registry.AGENTS, "M2", registry.AGENTS["M2"].__class__(**{**registry.AGENTS["M2"].__dict__, "recompute": boom}))
    v, checks = verifier.verify(acme, "M2", "ACME", AS_OF, ENVS["M2"]("ACME"))
    assert v.metrics == [] and all(c.reason.startswith("recompute failed: ValueError") for c in checks)


# --- debate ---------------------------------------------------------------------------------------------

def test_evidence_formatting_and_ids():
    ev = debate.build_evidence({("M2", "ACME"): ENVS["M2"]("ACME"), ("M3", "ACME"): ENVS["M3"]("ACME"), ("M7", "ACME"): ENVS["M7"]("ACME")})
    text = ev.render()
    assert "[M2:roce] ROCE, FY26: 23.7%" in text and "[M2:revenue] Revenue, FY26: Rs 206 crore" in text
    assert "[M7:pe] P/E (TTM profit): 27.3x" in text and "[M3:flag:0] alert: an auditor resigned in the last 3 years" in text
    assert ev.items["M2:finding:growth"].sources == ("https://example.invalid/data",)
    many = debate.build_evidence({("M2", "ACME"): ENVS["M2"]("ACME"), ("M2", "BETA"): ENVS["M2"]("BETA")})
    assert "ACME/M2:roce" in many.items and "BETA/M2:roce" in many.items


def test_validate_points():
    ev = debate.build_evidence({("M2", "ACME"): ENVS["M2"]("ACME")})
    kept, dropped = debate.validate([debate.Point(text="Returns are strong.", cites=["M2:roce", "M2:roce", "bogus"]),
                                     debate.Point(text="ROCE is 23.7%.", cites=["M2:roce"]),
                                     debate.Point(text="Nice.", cites=["bogus"])], ev, 5)
    assert [(p.text, p.cites) for p in kept] == [("Returns are strong.", ["M2:roce"])]
    assert dropped == ["contains a number", "cites no evidence id"]


def test_two_round_debate(llm):
    ev = debate.build_evidence({(a, "ACME"): ENVS[a]("ACME") for a in ENVS})
    result = debate.run_debate("stock ACME", ev)
    assert [c[0] for c in llm.calls] == ["bull_round1", "bear_round1", "bull_round2", "bear_round2"]
    assert "An auditor resignation is a governance concern." in llm.calls[2][1]  # bull answers the bear's round 1
    assert "Returns on capital are strong." in llm.calls[3][1]
    assert [p.text for p in result.bull] == ["Returns on capital are strong.", "The company is set to outperform the market next year."]
    assert result.dropped == ["contains a number", "cites no evidence id"]


# --- judge ----------------------------------------------------------------------------------------------

def outcome(agent, env=None, status="ok", public=False):
    return planner.Outcome(planner.Task(agent, "ACME", 10, ()), status, env, public, AS_OF)


def test_confidence_rule():
    hi = envelope("M2", "ACME", {"x": ("x", 1.0, None)})
    lo = envelope("M2", "ACME", {"x": ("x", 1.0, None)}, level="low")
    assert judge.confidence({1: outcome("M2", hi), 2: outcome("M7", hi)}, 2)[0] == "high"
    assert judge.confidence({1: outcome("M2", hi), 2: outcome("M7", status="failed")}, 2)[0] == "medium"
    assert judge.confidence({1: outcome("M2", hi), 2: outcome("M7"), 3: outcome("M3")}, 3)[0] == "low"
    assert judge.confidence({1: outcome("M2", lo), 2: outcome("M7", lo), 3: outcome("M3", hi)}, 3)[0] == "low"


def test_assemble_runs_compliance_last_and_tracks_public(llm):
    envs = {(a, "ACME"): ENVS[a]("ACME") for a in ("M2", "M3", "M5")}
    ev = debate.build_evidence(envs)
    deb = debate.run_debate("stock ACME", ev)
    summary, key_ids, notes, dropped = judge.synthesize("ACME", ev, deb)
    assert key_ids == ["M2:roce", "M5:finding:headline_6"]  # invented id ignored
    outcomes = {k: outcome(k[0], e, public=True) for k, e in envs.items()}
    r = judge.assemble("stock", "ACME", AS_OF, outcomes, 0, ev, summary, key_ids, notes, deb, dropped + len(deb.dropped))
    assert r.status == "published" and r.public_ok and r.disclaimer == DISCLAIMER
    assert [b.text for b in r.bull] == ["Returns on capital are strong."]  # "set to outperform" removed by compliance
    assert [i.id for i in r.key_items] == ["M2:roce"]  # the broker-upgrade headline removed by compliance
    assert [i.text for i in r.risks] == ["alert: an auditor resigned in the last 3 years"]
    # model points dropped: a digit, a fake cite, and the bear's round-2 point citing M7/M6 (not in this evidence)
    assert r.dropped == {"verifier": 0, "model_points": 3, "compliance": 2}
    outcomes[("M5", "ACME")] = outcome("M5", envs[("M5", "ACME")], public=False)
    assert not judge.assemble("stock", "ACME", AS_OF, outcomes, 0, ev, summary, key_ids, notes, deb, 0).public_ok


def test_assemble_blocks_when_the_compliance_llm_fails(llm):
    llm.replies["LLMVerdict"] = LLMError("openai 500")
    ev = debate.build_evidence({("M2", "ACME"): ENVS["M2"]("ACME")})
    r = judge.assemble("stock", "ACME", AS_OF, {("M2", "ACME"): outcome("M2", ENVS["M2"]("ACME"), public=True)}, 0, ev,
                       [debate.Point(text="Returns are strong.", cites=["M2:roce"])], ["M2:roce"], [], None, 0)
    assert r.status == "blocked" and not r.public_ok and r.summary == [] and "LLM compliance check failed" in r.blocked_reason


# --- end to end (DB) ------------------------------------------------------------------------------------

def test_page_report_end_to_end(acme, fake_agents, llm):
    fake_agents["tamper"]["M7"] = ("pe", 30.0)
    report = orchestrator.page_report(acme, "stock", "ACME", AS_OF)
    assert report.status == "published" and report.dropped["verifier"] == 1  # M7's P/E failed verification
    assert "M7 pe" not in json.dumps([i.id for i in report.key_items])
    assert report.agents == {f"{a} ACME": "ok" for a in ["M2", "M3", "M5", "M7", "M6"]}
    assert "M6: ROE rank: missing" in report.gaps and report.confidence == "medium"
    row = acme.execute("SELECT subject, kind, status, public_ok FROM reports").fetchone()
    assert row == ("stock:ACME", "stock", "published", False)  # dev data is never public
    logged = acme.execute("SELECT agent_id, metric_id, dropped FROM verification_log WHERE dropped").fetchall()
    assert logged == [("M7", "pe", True)]
    (mother_cost,) = acme.execute("SELECT cost_usd FROM agent_runs WHERE agent_id = 'MOTHER'").fetchone()
    assert mother_cost > 0  # debate, synthesis and compliance calls are costed on the mother run

    fake_agents["runs"].clear()
    again = orchestrator.page_report(acme, "stock", "ACME", AS_OF)
    assert fake_agents["runs"] == [] and set(again.agents.values()) == {"cached"}  # second run reads the cache
    assert acme.execute("SELECT count(*) FROM reports").fetchone() == (1,)  # one report per subject per day


def test_ask_end_to_end(acme, fake_agents, llm):
    llm.replies["AskIntent"] = {"intent": "stock_research", "subjects": ["ACME"], "agents": ["M2"]}
    llm.replies["Answer"] = {"answer": [{"text": "Returns on capital are high.", "cites": ["M2:roce"]}], "key_items": ["M2:roce"]}
    out = orchestrator.ask(acme, "How profitable is Acme?", AS_OF)
    assert out["status"] == "answered" and [p.text for p in out["report"].summary] == ["Returns on capital are high."]
    llm.replies["AskIntent"] = {"intent": "unsupported", "subjects": [], "agents": []}
    out = orchestrator.ask(acme, "Should I buy Nifty options?", AS_OF)
    assert out["status"] == "unsupported"
    rows = acme.execute("SELECT status FROM ask_answers ORDER BY id").fetchall()
    assert rows == [("answered",), ("unsupported",)]


def test_real_m2_through_the_mother(acme, llm):
    """Real M2 on the synthetic ACME financials: fresh run, verification passes, report stored."""
    from services.agents.desks.markets.tests.test_m2_fundamentals import acme_records
    from services.pipelines import fundamentals

    fundamentals.load(acme, fundamentals.build(acme_records()))
    llm.replies.update({k: {"points": [{"text": "Returns on capital are high.", "cites": ["M2:roce"]}]}
                        for k in ("bull_round1", "bear_round1", "bull_round2", "bear_round2")})
    llm.replies["Synthesis"] = {"summary": [{"text": "Returns on capital are high.", "cites": ["M2:roce"]}],
                                "key_items": ["M2:roce", "M2:quality_score"], "risk_notes": []}
    report = orchestrator.page_report(acme, "stock", "ACME", AS_OF, agents=["M2"])
    assert report.dropped["verifier"] == 0 and report.agents == {"M2 ACME": "ok"}
    assert [i.text for i in report.key_items] == ["ROCE (on average capital employed), FY26: 23.7%",
                                                  "Growth quality: share of 5 checks passed: 100.0%"]
