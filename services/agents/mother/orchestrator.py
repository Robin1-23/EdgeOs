"""The mother agent end to end: plan -> dispatch -> verify -> debate -> synthesize -> compliance -> store.

Page reports (nightly batch, one per subject per day) go to `reports`; ask-box answers go to `ask_answers`.
Each mother run is an agent_runs row (agent_id MOTHER), so its LLM cost is tracked like any specialist's.
Usage: uv run python -m services.agents.mother.orchestrator stock RELIANCE [--as-of 2026-10-27]
       uv run python -m services.agents.mother.orchestrator ask "How leveraged is RELIANCE?"
"""

import argparse
import dataclasses
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from db import DATABASE_URL
from services.agents.llm.client import LLMError
from services.agents.mother import debate, judge, planner, verifier
from services.agents.runlog import run_tracked

IST = ZoneInfo("Asia/Kolkata")
AGENT_ID = "MOTHER"


def _verify_all(conn, outcomes):
    """Verified outcomes (envelopes with unverifiable metrics removed) and every check."""
    verified, checks = {}, []
    for key, o in outcomes.items():
        if o.envelope is None:
            verified[key] = o
            continue
        env, cs = verifier.verify(conn, o.task.agent_id, o.task.subject, o.run_as_of, o.envelope)
        verified[key] = dataclasses.replace(o, envelope=env)
        checks += [(o.run_id, c) for c in cs]
    return verified, checks


def _log_checks(conn, checks, report_id=None, ask_id=None):
    if report_id is not None:
        conn.execute("DELETE FROM verification_log WHERE report_id = %s", (report_id,))
    for run_id, c in checks:
        conn.execute("""INSERT INTO verification_log (report_id, ask_id, run_id, agent_id, claim, metric_id, expected, actual,
                                                      dropped, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                     (report_id, ask_id, run_id, c.agent_id, c.claim, c.metric_id, c.recomputed, c.reported, c.dropped, c.reason))


def _deliberate(subject: str, ev: debate.Evidence):
    """Debate + synthesis; if the strong model fails, the report keeps its code-rendered parts and says so."""
    try:
        deb = debate.run_debate(subject, ev)
        summary, key_ids, notes, dropped = judge.synthesize(subject, ev, deb)
        return deb, summary, key_ids, notes, dropped + len(deb.dropped), None
    except LLMError as e:
        return None, [], [], [], 0, f"synthesis not produced: {e}"


def page_report(conn: psycopg.Connection, kind: str, subject: str, as_of: date, connect=None,
                use_llm_compliance: bool = True, agents: list[str] | None = None) -> judge.Report:
    plan = planner.page_plan(conn, kind, subject, as_of, agents)
    if plan.status != "ready":
        raise LookupError(plan.message)
    key = plan.subjects[0]

    def work(_run_id):
        outcomes = planner.dispatch(plan, conn, as_of, connect)
        verified, checks = _verify_all(conn, outcomes)
        ev = debate.build_evidence({k: o.envelope for k, o in verified.items() if o.envelope is not None})
        deb, summary, key_ids, notes, model_dropped, failure = _deliberate(f"{kind} {key}", ev)
        report = judge.assemble(kind, key, as_of, verified, sum(c.dropped for _, c in checks), ev, summary, key_ids, notes,
                                deb, model_dropped, use_llm_compliance)
        if failure:
            report.gaps.insert(0, failure)
        (report_id,) = conn.execute(
            """INSERT INTO reports (subject, as_of, kind, status, public_ok, report) VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (subject, as_of) DO UPDATE SET kind = EXCLUDED.kind, status = EXCLUDED.status,
                   public_ok = EXCLUDED.public_ok, report = EXCLUDED.report, created_at = now()
               RETURNING id""", (f"{kind}:{key}", as_of, kind, report.status, report.public_ok, Jsonb(report.model_dump(mode="json")))).fetchone()
        _log_checks(conn, checks, report_id=report_id)
        return report

    return run_tracked(conn, AGENT_ID, f"{kind}:{key}", datetime.combine(as_of, dtime(0), IST), work)


def ask(conn: psycopg.Connection, question: str, as_of: date, connect=None, use_llm_compliance: bool = True) -> dict:
    """Plan from the question, run what is needed, answer from verified evidence only. Returns the stored row."""
    def work(_run_id):
        plan = planner.ask_plan(conn, question, as_of)
        plan_json = Jsonb(dataclasses.asdict(plan))
        if plan.status != "ready":
            (aid,) = conn.execute("INSERT INTO ask_answers (question, as_of, plan, answer, status) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                                  (question, as_of, plan_json, Jsonb({"message": plan.message}), plan.status)).fetchone()
            return {"id": aid, "status": plan.status, "message": plan.message}
        outcomes = planner.dispatch(plan, conn, as_of, connect)
        verified, checks = _verify_all(conn, outcomes)
        ev = debate.build_evidence({k: o.envelope for k, o in verified.items() if o.envelope is not None})
        try:
            points, key_ids, dropped = judge.answer(question, ev)
            failure = None
        except LLMError as e:
            points, key_ids, dropped, failure = [], [], 0, f"answer not produced: {e}"
        report = judge.assemble(plan.kind, "+".join(plan.subjects), as_of, verified, sum(c.dropped for _, c in checks), ev,
                                points, key_ids, [], None, dropped, use_llm_compliance)
        if failure:
            report.gaps.insert(0, failure)
        status = "blocked" if report.status == "blocked" or not report.summary else "answered"
        (aid,) = conn.execute("INSERT INTO ask_answers (question, as_of, plan, answer, status, public_ok) VALUES (%s, %s, %s, %s, %s, %s)"
                              " RETURNING id", (question, as_of, plan_json, Jsonb(report.model_dump(mode="json")), status,
                                                report.public_ok)).fetchone()
        _log_checks(conn, checks, ask_id=aid)
        return {"id": aid, "status": status, "report": report}

    return run_tracked(conn, AGENT_ID, "ask", datetime.combine(as_of, dtime(0), IST), work)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["stock", "fund", "ask"])
    ap.add_argument("subject", help="symbol / ISIN / AMFI code, or the question for ask")
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        def connect():
            return psycopg.connect(DATABASE_URL, autocommit=True)
        if args.mode == "ask":
            out = ask(conn, args.subject, args.as_of, connect)
            print(out["report"].model_dump_json(indent=2) if "report" in out else out)
        else:
            print(page_report(conn, args.mode, args.subject, args.as_of, connect).model_dump_json(indent=2))
