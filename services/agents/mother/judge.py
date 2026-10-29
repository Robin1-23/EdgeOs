"""Mother agent, judge: collect, synthesize and publish. The compliance guard runs last on everything.

The strong model writes a short summary and risk notes (cited, no digits) and picks which evidence items to
feature; code renders those items, lists every flag and gap, and computes the report's confidence from agent
coverage and the agents' own confidence. Every text that reaches the report passes compliance.guard_many; if the
guard's LLM pass fails, the report is blocked. A report is public only if every agent output behind it is.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from services.agents.llm.client import call_json
from services.agents.mother.compliance import DISCLAIMER, guard_many
from services.agents.mother.debate import DebateResult, Evidence, Point, render_points, validate
from services.agents.mother.planner import Outcome

MAX_SUMMARY, MAX_RISK_NOTES, MAX_KEY_ITEMS = 5, 4, 8


class CitedText(BaseModel):
    text: str
    cites: list[str]


class ReportItem(BaseModel):
    id: str
    agent_id: str
    subject: str
    text: str
    sources: list[str]


class Report(BaseModel):
    kind: str
    subject: str
    as_of: date
    status: Literal["published", "blocked"]
    public_ok: bool
    agents: dict[str, str]  # "agent subject" -> cached | ok | failed | timed_out
    summary: list[CitedText]
    key_items: list[ReportItem]
    bull: list[CitedText]
    bear: list[CitedText]
    risks: list[ReportItem]
    risk_notes: list[CitedText]
    gaps: list[str]
    confidence: Literal["high", "medium", "low"]
    confidence_reason: str
    disclaimer: str
    dropped: dict[str, int]  # how many claims each stage removed: verifier, model_points, compliance
    blocked_reason: str | None = None


class Synthesis(BaseModel):
    summary: list[Point] = Field(description="2-5 sentences describing the subject, no digits, each citing evidence ids")
    key_items: list[str] = Field(description="up to 8 evidence ids most worth showing")
    risk_notes: list[Point] = Field(description="up to 4 risks or open questions, no digits, citing evidence ids")


class Answer(BaseModel):
    answer: list[Point] = Field(description="2-6 sentences answering the question from the evidence, no digits, cited")
    key_items: list[str] = Field(description="up to 8 evidence ids that support the answer")


JUDGE_SYSTEM = (
    "You write the summary of a research page about one Indian listed company or mutual fund for a site that "
    "describes and never advises. Use only the numbered evidence and the bull and bear points. Never recommend "
    "buying, selling, holding, switching or exiting; never give target prices, fair values, forecasts, return "
    "expectations or ratings. Do not write digits: cite evidence ids instead. Every sentence cites evidence."
)
ASK_SYSTEM = (
    "You answer an investor's research question about Indian listed companies or mutual funds using only the "
    "numbered evidence. Describe what the evidence shows; never recommend actions, give target prices, forecasts, "
    "return expectations or ratings, and do not give personal advice. Do not write digits: cite evidence ids. If "
    "the evidence does not answer the question, say so."
)


def confidence(outcomes: dict, planned: int) -> tuple[str, str]:
    """Code rule: missing agents cap confidence; otherwise the agents' own levels decide."""
    present = [o for o in outcomes.values() if o.envelope is not None]
    missing = planned - len(present)
    levels = [o.envelope.confidence.level for o in present]
    if not present or missing * 2 > planned or levels.count("low") * 2 > len(levels):
        return "low", f"{len(present)} of {planned} agents reported; {levels.count('low')} with low confidence"
    if missing == 0 and all(lv == "high" for lv in levels):
        return "high", f"all {planned} agents reported with high confidence"
    return "medium", f"{len(present)} of {planned} agents reported; levels {', '.join(sorted(set(levels)))}"


def _items(ev: Evidence, ids: list[str]) -> list[ReportItem]:
    out = []
    for i in dict.fromkeys(ids):
        if i in ev.items:
            it = ev.items[i]
            out.append(ReportItem(id=it.id, agent_id=it.agent_id, subject=it.subject, text=it.text, sources=[s for s in it.sources if s]))
    return out


def assemble(kind: str, subject: str, as_of: date, outcomes: dict[tuple[str, str], Outcome], verifier_dropped: int,
             ev: Evidence, synth_summary: list[Point], key_ids: list[str], risk_notes: list[Point], debate: DebateResult | None,
             model_dropped: int, use_llm_compliance: bool = True) -> Report:
    gaps = [f"{a} ({s}): {o.status}{' - ' + o.error if o.error else ''}" for (a, s), o in outcomes.items() if o.envelope is None]
    gaps += [f"{a}: {g.item}: {g.reason}" for (a, _), o in outcomes.items() if o.envelope for g in o.envelope.gaps]
    risks = _items(ev, [i for i, it in ev.items.items() if it.kind == "flag"])
    key_items = _items(ev, key_ids[:MAX_KEY_ITEMS])
    sections = {
        "summary": [CitedText(text=p.text, cites=p.cites) for p in synth_summary],
        "bull": [CitedText(text=p.text, cites=p.cites) for p in (debate.bull if debate else [])],
        "bear": [CitedText(text=p.text, cites=p.cites) for p in (debate.bear if debate else [])],
        "risk_notes": [CitedText(text=p.text, cites=p.cites) for p in risk_notes],
    }
    # compliance guard last: every text in one pass (rules per item, one LLM call for all remaining sentences)
    texts = [c.text for sec in sections.values() for c in sec] + [i.text for i in key_items] + [i.text for i in risks]
    g = guard_many(texts, use_llm=use_llm_compliance)
    sanitized = iter(g.texts)
    for name, sec in sections.items():
        sections[name] = [c.model_copy(update={"text": t}) for c in sec if (t := next(sanitized)) is not None]
    key_items = [i.model_copy(update={"text": t}) for i in key_items if (t := next(sanitized)) is not None]
    risks = [i.model_copy(update={"text": t}) for i in risks if (t := next(sanitized)) is not None]
    level, reason = confidence(outcomes, len(outcomes))
    public = bool(outcomes) and all(o.public_ok for o in outcomes.values() if o.envelope is not None) and \
        all(o.envelope is not None for o in outcomes.values())
    return Report(kind=kind, subject=subject, as_of=as_of, status="blocked" if g.blocked else "published",
                  public_ok=public and not g.blocked, agents={f"{a} {s}": o.status for (a, s), o in outcomes.items()},
                  key_items=key_items, risks=risks, gaps=gaps, confidence=level, confidence_reason=reason,
                  disclaimer=DISCLAIMER, blocked_reason=g.blocked,
                  dropped={"verifier": verifier_dropped, "model_points": model_dropped, "compliance": len(g.removed)},
                  **sections)


def synthesize(subject: str, ev: Evidence, debate: DebateResult) -> tuple[list[Point], list[str], list[Point], int]:
    prompt = (f"Subject: {subject}\n\nEvidence:\n{ev.render()}\n\nBull points:\n{render_points(debate.bull)}\n\n"
              f"Bear points:\n{render_points(debate.bear)}")
    s = call_json("strong", JUDGE_SYSTEM, prompt, Synthesis).value
    summary, d1 = validate(s.summary, ev, MAX_SUMMARY)
    notes, d2 = validate(s.risk_notes, ev, MAX_RISK_NOTES)
    return summary, [i for i in s.key_items if i in ev.items], notes, len(d1) + len(d2)


def answer(question: str, ev: Evidence) -> tuple[list[Point], list[str], int]:
    a = call_json("strong", ASK_SYSTEM, f"Question: {question}\n\nEvidence:\n{ev.render()}", Answer).value
    points, dropped = validate(a.answer, ev, 6)
    return points, [i for i in a.key_items if i in ev.items], len(dropped)
