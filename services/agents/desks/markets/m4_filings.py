"""M4 Filings and concalls: plain-language restatement of a company's recent exchange filings.

The LLM (specialist tier) restates each filing as short statements, each with a verbatim quote. Code then keeps
only statements whose quote is found in the filing text and whose numbers all appear in that quote, so nothing
reaches users that the filing does not say (CLAUDE.md rules 1 and 4). Event tags and tone are labels from fixed
lists; the tone change is computed by code from two filings' labels. Every finding links to the filing URL.
One summary per filing is cached by text hash + model + prompt version, so nightly runs only pay for new text.
Usage: uv run python -m services.agents.desks.markets.m4_filings RELIANCE [--as-of 2026-10-27]
"""

import argparse
import re
from datetime import date, datetime, time as dtime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.envelope import Confidence, Envelope, Finding, Flag, Gap, Metric, Source
from services.agents.llm import config as llm_config
from services.agents.llm.client import LLMError, call_json
from services.agents.runlog import run_logged

AGENT_ID = "M4"
IST = ZoneInfo("Asia/Kolkata")
PROMPT_VERSION = "m4-v1"
LOOKBACK_DAYS = 120
MAX_FILING_CHARS = 60_000  # ~15k tokens; longer filings are summarised from their start and say so
MIN_QUOTE_CHARS = 12
MAX_FILINGS = 20
EventTag = Literal["results", "dividend", "buyback", "merger_acquisition", "fund_raise", "credit_rating",
                   "management_change", "auditor_change", "order_win", "litigation", "regulatory_action", "pledge",
                   "insider_trade", "agm_egm", "guidance_update", "other"]
Tone = Literal["positive", "neutral", "negative", "mixed", "not_applicable"]
MATERIAL_TAGS = {"auditor_change", "regulatory_action", "litigation", "pledge", "management_change", "credit_rating"}
TONE_CATEGORIES = {"results", "concall"}


class M4Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: str = Field(min_length=1)  # NSE symbol or ISIN
    as_of: date


class Statement(BaseModel):
    text: str = Field(description="one plain-English sentence restating the filing, at most 40 words")
    quote: str = Field(description="a short passage copied verbatim from the filing that supports the sentence, "
                                   "including every number the sentence uses")


class FilingSummary(BaseModel):
    event_tag: EventTag
    summary: list[Statement] = Field(description="1-5 statements of what the filing says")
    guidance: list[Statement] = Field(description="forward-looking statements made by the company or management; "
                                                  "empty if none")
    tone: Tone = Field(description="tone of management's own language; not_applicable for routine filings")
    tone_evidence: list[str] = Field(description="1-3 verbatim quotes showing the tone; empty if not_applicable")


SYSTEM = (
    "You restate an Indian listed company's stock-exchange filing in plain English. Only restate what the filing "
    "says: never add facts, opinions, forecasts, ratings, price views or advice, and never compute new numbers. "
    "For every statement copy a short verbatim quote from the filing that supports it, including every number "
    "you use. Put only forward-looking statements made by the company or its management in 'guidance', "
    "attributed to them. 'tone' describes management's own language, with verbatim quotes as evidence."
)


def prompt(symbol: str, f: dict) -> str:
    text = f["text"][:MAX_FILING_CHARS]
    return (f"Company: {symbol}\nFiling title: {f['title']}\nCategory: {f['category']}\n"
            f"Filed: {f['filed_at']:%Y-%m-%d}\n\n--- FILING TEXT ---\n{text}")


# --- verification (deterministic) -----------------------------------------------------------------------

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?(?:\s*(?:%|per\s*cent|percent))?")  # "22%" is not "22 crore"
_FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "₹": "rs "})


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_FOLD)).strip().lower()


def numbers(s: str) -> set[str]:
    return {re.sub(r"\s*(?:per\s*cent|percent)$", "%", n.replace(",", "").rstrip(".")).replace(" %", "%")
            for n in _NUM.findall(s.lower())}


def check_statement(st: Statement, text: str) -> str | None:
    """None if the statement is supported by the filing, else the reason it is dropped."""
    q = normalize(st.quote)
    if len(q) < MIN_QUOTE_CHARS or q not in normalize(text):
        return "quote not found in the filing"
    if missing := numbers(st.text) - numbers(st.quote):
        return f"numbers not in the quote: {', '.join(sorted(missing))}"
    return None


# --- envelope -------------------------------------------------------------------------------------------

def compute(isin: str, symbol: str | None, filings: list[dict], summaries: dict[int, FilingSummary | str],
            as_of: date, retrieved_at: datetime) -> Envelope:
    """filings: [{id, filed_at, category, title, url, text}] newest first; summaries: filing id -> summary, or the
    reason it could not be produced."""
    subject = symbol or isin
    metrics, findings, flags, gaps, sources = [], [], [], [], []
    tones: list[tuple[datetime, str, int]] = []

    for f in filings:
        fid, sid = f["id"], f"filing_{f['id']}"
        label = f"{f['title']} ({f['filed_at']:%d %b %Y})"
        if not f["text"]:
            gaps.append(Gap(item=label, reason="filing text not available"))
            continue
        s = summaries.get(fid)
        if not isinstance(s, FilingSummary):
            gaps.append(Gap(item=label, reason=f"summary not produced: {s or 'not attempted'}"))
            continue
        if len(f["text"]) > MAX_FILING_CHARS:
            gaps.append(Gap(item=label, reason=f"only the first {MAX_FILING_CHARS:,} characters were summarised"))
        sources.append(Source(id=sid, kind="filing", ref=f["url"], retrieved_at=retrieved_at))
        metrics.append(Metric(id=f"event_{fid}", name=f"Event type: {label}", value=s.event_tag, unit=None,
                              formula_id=f"llm_event_tag:{PROMPT_VERSION}", source_query_id=sid))
        dropped = []
        for kind, statements in (("summary", s.summary), ("guidance", s.guidance)):
            for i, st in enumerate(statements):
                if reason := check_statement(st, f["text"]):
                    dropped.append(reason)
                    continue
                text = f"Management guidance: {st.text}" if kind == "guidance" else st.text
                findings.append(Finding(id=f"{kind}_{fid}_{i}", text=text, metric_ids=[f"event_{fid}"]))
        if dropped:
            gaps.append(Gap(item=label, reason=f"{len(dropped)} statement(s) dropped: {'; '.join(sorted(set(dropped)))}"))
        if s.tone != "not_applicable":
            evidence = [q for q in s.tone_evidence if len(normalize(q)) >= MIN_QUOTE_CHARS and normalize(q) in normalize(f["text"])]
            if evidence:
                metrics.append(Metric(id=f"tone_{fid}", name=f"Management tone: {label}", value=s.tone, unit=None,
                                      formula_id=f"llm_tone:{PROMPT_VERSION}", source_query_id=sid))
                if f["category"] in TONE_CATEGORIES:
                    tones.append((f["filed_at"], s.tone, fid))
            else:
                gaps.append(Gap(item=label, reason="tone not shown: no evidence quote found in the filing"))
        if s.event_tag in MATERIAL_TAGS:
            flags.append(Flag(severity="watch", reason=f"filing tagged {s.event_tag.replace('_', ' ')}: {label}",
                              metric_ids=[f"event_{fid}"]))

    tones.sort()
    if len(tones) >= 2:
        (_, prev, _), (_, cur, cur_id) = tones[-2], tones[-1]
        metrics.append(Metric(id="tone_change", name="Management tone, previous results/concall to latest",
                              value=f"{prev} -> {cur}", unit=None, formula_id="tone_change", source_query_id=f"filing_{cur_id}"))
        if prev != cur:
            flags.append(Flag(severity="info", reason=f"management tone changed from {prev} to {cur}", metric_ids=["tone_change"]))
    elif tones:
        gaps.append(Gap(item="tone change", reason="needs two results/concall filings with a verified tone"))

    if not filings:
        gaps.append(Gap(item="filings", reason=f"no filings in the {LOOKBACK_DAYS} days to {as_of}"))
    if not findings:
        confidence = Confidence(level="low", reason="no verified statement from any filing")
    elif gaps:
        confidence = Confidence(level="medium", reason=f"{len(findings)} verified statements; {len(gaps)} gap(s)")
    else:
        confidence = Confidence(level="high", reason=f"every statement verified against {len(sources)} filing(s)")
    return Envelope(agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(as_of, dtime(0), IST), metrics=metrics,
                    findings=findings, flags=flags, sources=sources, confidence=confidence, gaps=gaps)


# --- run ------------------------------------------------------------------------------------------------

def load_inputs(conn: psycopg.Connection, subject: str, as_of: date):
    """(isin, symbol, filings newest first, providers per filing source id)."""
    row = conn.execute("SELECT id, isin, nse_symbol FROM securities WHERE nse_symbol = %s OR isin = %s", (subject, subject)).fetchone()
    if row is None:
        raise LookupError(f"unknown security {subject!r}")
    sid, isin, symbol = row
    end = datetime.combine(as_of + timedelta(days=1), dtime(0), IST)
    filings, providers = [], {}
    for fid, filed_at, category, title, url, text, sha, src in conn.execute(
        """SELECT id, filed_at, category, title, url, body_text, text_sha256, source FROM filings
           WHERE security_id = %s AND filed_at >= %s AND filed_at < %s ORDER BY filed_at DESC LIMIT %s""",
        (sid, end - timedelta(days=LOOKBACK_DAYS), end, MAX_FILINGS)):
        filings.append({"id": fid, "filed_at": filed_at, "category": category, "title": title, "url": url,
                        "text": text, "sha": sha})
        providers[f"filing_{fid}"] = [src]
    return isin, symbol, filings, providers


def summarize(conn: psycopg.Connection, symbol: str, filings: list[dict], cache_only: bool = False) -> dict[int, FilingSummary | str]:
    """Cached summary per filing, else one specialist call; an LLMError becomes that filing's gap reason.
    cache_only (the verifier's recompute) never calls the LLM: uncached filings are reported as not cached."""
    model, out = llm_config.MODELS["specialist"], {}
    for f in filings:
        if not f["text"]:
            continue
        cached = conn.execute("SELECT summary FROM filing_summaries WHERE filing_id = %s AND text_sha256 = %s AND model = %s"
                              " AND prompt_version = %s", (f["id"], f["sha"], model, PROMPT_VERSION)).fetchone()
        if cached:
            out[f["id"]] = FilingSummary.model_validate(cached[0])
            continue
        if cache_only:
            out[f["id"]] = "no cached summary"
            continue
        try:
            s = call_json("specialist", SYSTEM, prompt(symbol, f), FilingSummary).value
        except LLMError as e:
            out[f["id"]] = str(e)
            continue
        conn.execute("INSERT INTO filing_summaries (filing_id, text_sha256, model, prompt_version, summary) VALUES (%s, %s, %s, %s, %s)"
                     " ON CONFLICT DO NOTHING", (f["id"], f["sha"], model, PROMPT_VERSION, Jsonb(s.model_dump(mode="json"))))
        out[f["id"]] = s
    return out


def run(conn: psycopg.Connection, inp: M4Input) -> Envelope:
    """Summarise (cached), verify, and log to agent_runs/agent_outputs; LLM cost lands on the run."""
    def go():
        isin, symbol, filings, providers = load_inputs(conn, inp.subject, inp.as_of)
        summaries = summarize(conn, symbol or isin, filings)
        return compute(isin, symbol, filings, summaries, inp.as_of, datetime.now(IST)), providers

    return run_logged(conn, AGENT_ID, inp.subject, datetime.combine(inp.as_of, dtime(0), IST), go)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subject", help="NSE symbol or ISIN")
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        print(run(conn, M4Input(subject=args.subject, as_of=args.as_of)).model_dump_json(indent=2))
