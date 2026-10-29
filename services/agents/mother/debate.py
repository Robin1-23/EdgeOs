"""Mother agent, debate: a bull and a bear sub-agent argue from verified evidence only, two rounds, citing ids.

Evidence is rendered by code: every verified metric (with its value formatted here), finding and flag gets an id.
The strong model writes the points; code keeps a point only if it cites at least one real evidence id and
contains no digits: numbers on the page always come from the cited metrics, never from model text (rule 1).
"""

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from services.agents.envelope import Envelope
from services.agents.llm.client import call_json

MAX_POINTS_R1, MAX_POINTS_R2 = 5, 3
_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class Item:
    id: str
    agent_id: str
    subject: str
    kind: str  # metric | finding | flag
    text: str  # rendered by code
    sources: tuple[str, ...]  # URLs / dataset refs behind it


@dataclass
class Evidence:
    items: dict[str, Item] = field(default_factory=dict)

    def render(self) -> str:
        return "\n".join(f"[{i.id}] {i.text}" for i in self.items.values())


class Point(BaseModel):
    text: str = Field(description="one sentence, no digits; refer to evidence by id instead of restating numbers")
    cites: list[str] = Field(description="evidence ids this point rests on, e.g. M2:roce")


class Case(BaseModel):
    points: list[Point]


def fmt(value, unit: str | None) -> str:
    """Display value for a metric (the only place numbers are formatted for model-facing text)."""
    if isinstance(value, str):
        return value
    if unit == "ratio":
        return f"{value:.1%}"
    if unit == "INR cr":
        return f"Rs {value:,.0f} crore"
    if unit == "INR":
        return f"Rs {value:,.2f}"
    if unit == "x":
        return f"{value:.1f}x"
    if unit in ("%", "pp", "points"):
        return f"{value:.1f} {unit}"
    return f"{value:g}"


def build_evidence(envelopes: dict[tuple[str, str], Envelope]) -> Evidence:
    """Ids are agent:metric_id / agent:finding:id / agent:flag:n; prefixed with the subject when several subjects."""
    ev = Evidence()
    many = len({s for _, s in envelopes}) > 1
    for (agent, subject), env in envelopes.items():
        pre = f"{subject}/{agent}" if many else agent
        src = {s.id: s.ref for s in env.sources}
        for m in env.metrics:
            ev.items[f"{pre}:{m.id}"] = Item(f"{pre}:{m.id}", agent, subject, "metric", f"{m.name}: {fmt(m.value, m.unit)}",
                                             (src.get(m.source_query_id, ""),))
        by_metric = {m.id: m for m in env.metrics}
        for f in env.findings:
            refs = tuple(dict.fromkeys(src.get(by_metric[mid].source_query_id, "") for mid in f.metric_ids))
            ev.items[f"{pre}:finding:{f.id}"] = Item(f"{pre}:finding:{f.id}", agent, subject, "finding", f.text, refs)
        for n, fl in enumerate(env.flags):
            refs = tuple(dict.fromkeys(src.get(by_metric[mid].source_query_id, "") for mid in fl.metric_ids)) or tuple(src.values())[:1]
            ev.items[f"{pre}:flag:{n}"] = Item(f"{pre}:flag:{n}", agent, subject, "flag", f"{fl.severity}: {fl.reason}", refs)
    return ev


def validate(points: list[Point], ev: Evidence, limit: int) -> tuple[list[Point], list[str]]:
    """(points kept with only real cites, reasons for the ones dropped)."""
    kept, dropped = [], []
    for p in points[:limit]:
        cites = [c for c in dict.fromkeys(p.cites) if c in ev.items]
        if _DIGIT.search(p.text):
            dropped.append("contains a number")
        elif not cites:
            dropped.append("cites no evidence id")
        else:
            kept.append(Point(text=p.text.strip(), cites=cites))
    dropped += ["over the point limit"] * max(0, len(points) - limit)
    return kept, dropped


SIDE = {
    "bull": "the strengths: the positive facts in the evidence (growth, margins, returns on capital, balance "
            "sheet, cash generation, governance, positive developments)",
    "bear": "the weaknesses and risks: the negative facts in the evidence (slowing growth, thin or falling margins, "
            "leverage, weak cash conversion, governance red flags, negative developments, data gaps)",
}


def system(side: str) -> str:
    return (f"You argue the {side} case about one Indian listed company or fund, presenting {SIDE[side]}. Use only "
            "the numbered evidence given. Describe facts; never recommend buying, selling or holding, never give "
            "a price target, fair value or return forecast, never give a rating. Do not write any digits: cite "
            "evidence ids instead of restating numbers. Every point must cite at least one evidence id.")


def render_points(points: list[Point]) -> str:
    return "\n".join(f"- {p.text} (cites: {', '.join(p.cites)})" for p in points) or "- (none)"


@dataclass
class DebateResult:
    bull: list[Point]
    bear: list[Point]
    dropped: list[str]


def run_debate(subject: str, ev: Evidence) -> DebateResult:
    """Round 1: each side argues from the evidence. Round 2: each side answers the other's round-1 points."""
    evidence = ev.render()
    r1, dropped = {}, []
    for side in ("bull", "bear"):
        case = call_json("strong", system(side), f"Subject: {subject}\n\nEvidence:\n{evidence}\n\n"
                         f"Give at most {MAX_POINTS_R1} points.", Case, schema_name=f"{side}_round1").value
        r1[side], d = validate(case.points, ev, MAX_POINTS_R1)
        dropped += d
    final = {}
    for side, other in (("bull", "bear"), ("bear", "bull")):
        case = call_json("strong", system(side), f"Subject: {subject}\n\nEvidence:\n{evidence}\n\nThe {other} side argued:\n"
                         f"{render_points(r1[other])}\n\nAnswer those points with at most {MAX_POINTS_R2} new points.",
                         Case, schema_name=f"{side}_round2").value
        r2, d = validate(case.points, ev, MAX_POINTS_R2)
        dropped += d
        final[side] = r1[side] + r2
    return DebateResult(final["bull"], final["bear"], dropped)
