"""M5 News and sentiment: which headlines mention a company, how much coverage, and their tone.

Code matches headlines to the company (whole-word name / symbol / alias matching) and computes every number:
coverage counts and change, publisher count, and net sentiment from labels. The LLM (specialist tier) only
labels each headline's sentiment toward the company and its event type, from fixed lists; labels are cached per
headline and company. Headlines are shown verbatim with their link (headlines-and-links licence terms).
Usage: uv run python -m services.agents.desks.markets.m5_news RELIANCE [--alias "RIL"] [--as-of 2026-10-27]
"""

import argparse
import re
from datetime import date, datetime, time as dtime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.envelope import Confidence, Envelope, Finding, Flag, Gap, Metric, Source
from services.agents.llm import config as llm_config
from services.agents.llm.client import LLMError, call_json
from services.agents.runlog import run_logged

AGENT_ID = "M5"
IST = ZoneInfo("Asia/Kolkata")
PROMPT_VERSION = "m5-v1"
WINDOW_DAYS, RECENT_DAYS = 30, 7
SHOWN_HEADLINES = 20
MAX_LABEL_BATCH = 50
SPIKE_MIN, SPIKE_RATIO = 5, 3.0  # 7-day count at least 5 and 3x the prior 30 days' weekly rate
NEGATIVE_SKEW, MIN_LABELLED = -0.5, 5
NAME_SUFFIX = re.compile(r"[\s,]+(?:limited|ltd\.?|pvt\.?\s+ltd\.?|private\s+limited)$", re.IGNORECASE)
NewsTag = Literal["results", "guidance", "order_win", "deal_or_merger", "fund_raise", "management_change",
                  "regulatory_or_legal", "rating_or_broker_action", "product_or_expansion", "governance",
                  "market_move", "sector_or_macro", "other"]


class M5Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: str = Field(min_length=1)  # NSE symbol or ISIN
    as_of: date
    aliases: list[str] = []  # extra names the company is known by in headlines (brands, short names)


class HeadlineLabel(BaseModel):
    index: int
    sentiment: Literal["positive", "neutral", "negative"]
    event_tag: NewsTag


class NewsLabels(BaseModel):
    labels: list[HeadlineLabel]


SYSTEM = (
    "You label news headlines about an Indian listed company. For each numbered headline give: sentiment, the "
    "tone of the news for the company's business as reported (positive, neutral or negative; not a view on the "
    "share price or on what investors should do), and event_tag, the kind of news. Use only the headline text."
)


def prompt(name: str, headlines: list[tuple[int, str]]) -> str:
    return f"Company: {name}\n\n" + "\n".join(f"[{i}] {h}" for i, h in headlines)


# --- matching and maths (deterministic) -----------------------------------------------------------------

def aliases_for(name: str | None, symbol: str | None, extra: list[str]) -> tuple[list[str], list[str]]:
    """(case-insensitive name aliases, case-sensitive symbols). 'Acme Widgets Limited' -> 'Acme Widgets'."""
    names = [NAME_SUFFIX.sub("", n).strip() for n in [name, *extra] if n and n.strip()]
    names = [n for n in dict.fromkeys(names) if len(n) >= 3]
    return names, [symbol] if symbol and len(symbol) >= 3 else []


def mentions(headline: str, names: list[str], symbols: list[str]) -> bool:
    """Whole-word match: names ignoring case, symbols exactly ('ITC' matches 'ITC Q2', not 'itc' inside words)."""
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", headline, re.IGNORECASE) for n in names) or \
        any(re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", headline) for s in symbols)


def net_sentiment(labels: list[str]) -> float:
    """net_sentiment: (positive - negative) / labelled headlines, in [-1, 1]."""
    return (labels.count("positive") - labels.count("negative")) / len(labels)


def coverage_change(recent: int, previous: int) -> float | None:
    """coverage_change_30d: last 30 days' headline count / the 30 days before, minus 1; None without a base."""
    return recent / previous - 1 if previous else None


# --- envelope -------------------------------------------------------------------------------------------

def compute(isin: str, symbol: str | None, matched: list[dict], labels: dict[int, tuple[str, str]], label_error: str | None,
            as_of: date, retrieved_at: datetime) -> Envelope:
    """matched: headlines mentioning the company in the 60 days to as_of, [{id, published_at, publisher, headline, url}];
    labels: news id -> (sentiment, event_tag)."""
    subject = symbol or isin
    end = datetime.combine(as_of + timedelta(days=1), dtime(0), IST)
    in_days = lambda h, a, b: end - timedelta(days=b) <= h["published_at"] < end - timedelta(days=a)  # noqa: E731
    last30 = sorted((h for h in matched if in_days(h, 0, WINDOW_DAYS)), key=lambda h: h["published_at"], reverse=True)
    prev30 = [h for h in matched if in_days(h, WINDOW_DAYS, 2 * WINDOW_DAYS)]
    last7 = [h for h in last30 if in_days(h, 0, RECENT_DAYS)]
    metrics, findings, flags, gaps = [], [], [], []
    sources = [Source(id="coverage", kind="dataset", retrieved_at=retrieved_at,
                      ref=f"news_items whose headline names {subject}, {2 * WINDOW_DAYS} days to {as_of}")]

    def add(mid, name, value, formula, src="coverage", unit=None):
        metrics.append(Metric(id=mid, name=name, value=value, unit=unit, formula_id=formula, source_query_id=src))

    add("headlines_7d", "Headlines naming the company, last 7 days", float(len(last7)), "count_7d")
    add("headlines_30d", "Headlines naming the company, last 30 days", float(len(last30)), "count_30d")
    add("headlines_prev_30d", "Headlines naming the company, the 30 days before", float(len(prev30)), "count_prev_30d")
    add("publishers_30d", "Publishers covering the company, last 30 days", float(len({h["publisher"] for h in last30})), "distinct_30d")
    if (chg := coverage_change(len(last30), len(prev30))) is not None:
        add("coverage_change_30d", "Change in headline count vs the previous 30 days", chg, "coverage_change_30d", unit="ratio")
    else:
        gaps.append(Gap(item="coverage change", reason="no headlines in the previous 30 days to compare with"))
    weekly_prev = len(prev30) / (WINDOW_DAYS / RECENT_DAYS)
    if len(last7) >= SPIKE_MIN and len(last7) >= SPIKE_RATIO * max(weekly_prev, 1e-9):
        flags.append(Flag(severity="info", reason=f"{len(last7)} headlines in 7 days, above the usual weekly rate",
                          metric_ids=["headlines_7d", "headlines_prev_30d"]))

    if label_error:
        gaps.append(Gap(item="headline labels", reason=f"labelling failed: {label_error}"))
    labelled = [labels[h["id"]][0] for h in last30 if h["id"] in labels]
    if unlabelled := len(last30) - len(labelled):
        gaps.append(Gap(item="headline labels", reason=f"{unlabelled} of {len(last30)} recent headlines not labelled"))
    if labelled:
        net = net_sentiment(labelled)
        add("net_sentiment_30d", f"Net headline sentiment, last 30 days ({len(labelled)} labelled)", net, "net_sentiment", "labels", "ratio")
        for s in ("positive", "neutral", "negative"):
            add(f"{s}_30d", f"{s.title()} headlines, last 30 days", float(labelled.count(s)), "count_label", "labels")
        sources.append(Source(id="labels", kind="dataset", retrieved_at=retrieved_at,
                              ref=f"news_labels ({llm_config.MODELS['specialist']}, {PROMPT_VERSION}) for {subject}"))
        if len(labelled) >= MIN_LABELLED and net <= NEGATIVE_SKEW:
            flags.append(Flag(severity="watch", reason=f"most recent headlines are negative in tone ({labelled.count('negative')} of "
                                                       f"{len(labelled)})", metric_ids=["net_sentiment_30d"]))

    for h in last30[:SHOWN_HEADLINES]:
        sid = f"news_{h['id']}"
        sources.append(Source(id=sid, kind="api", ref=h["url"], retrieved_at=retrieved_at))
        if h["id"] in labels:
            sentiment, tag = labels[h["id"]]
            add(sid, f"Headline tone and type: {h['headline']}", f"{sentiment}; {tag}", f"llm_label:{PROMPT_VERSION}", sid)
        else:
            add(sid, f"Headline: {h['headline']}", "unlabelled", "headline", sid)
        findings.append(Finding(id=f"headline_{h['id']}", text=f"{h['publisher']}, {h['published_at']:%d %b %Y}: “{h['headline']}”",
                                metric_ids=[sid]))

    if not last30:
        gaps.append(Gap(item="headlines", reason=f"no headlines naming the company in the {WINDOW_DAYS} days to {as_of}"))
    if not last30:
        confidence = Confidence(level="low", reason="no recent coverage found")
    elif gaps:
        confidence = Confidence(level="medium", reason=f"{len(last30)} headlines; {len(gaps)} gap(s)")
    else:
        confidence = Confidence(level="high", reason=f"{len(last30)} headlines, all labelled")
    used = {m.source_query_id for m in metrics}
    return Envelope(agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(as_of, dtime(0), IST), metrics=metrics,
                    findings=findings, flags=flags, confidence=confidence, gaps=gaps,
                    sources=[s for s in sources if s.id in used])


# --- run ------------------------------------------------------------------------------------------------

def load_inputs(conn: psycopg.Connection, inp: M5Input):
    """(security id, isin, symbol, display name, matched headlines, providers)."""
    row = conn.execute("SELECT id, isin, nse_symbol, name FROM securities WHERE nse_symbol = %s OR isin = %s",
                       (inp.subject, inp.subject)).fetchone()
    if row is None:
        raise LookupError(f"unknown security {inp.subject!r}")
    sec_id, isin, symbol, name = row
    names, symbols = aliases_for(name, symbol, inp.aliases)
    end = datetime.combine(inp.as_of + timedelta(days=1), dtime(0), IST)
    matched, providers = [], {"coverage": set()}
    # ponytail: alias matching in Python over the 60-day window; move to Postgres full-text search at scale.
    for nid, published_at, publisher, headline, url, src in conn.execute(
        "SELECT id, published_at, publisher, headline, url, source FROM news_items WHERE published_at >= %s AND published_at < %s",
        (end - timedelta(days=2 * WINDOW_DAYS), end)):
        if mentions(headline, names, symbols):
            matched.append({"id": nid, "published_at": published_at, "publisher": publisher, "headline": headline, "url": url})
            providers["coverage"].add(src)
            providers[f"news_{nid}"] = [src]
    providers["labels"] = set(providers["coverage"])
    display = names[0] if names else (symbol or isin)
    return sec_id, isin, symbol, display, matched, {k: sorted(v) for k, v in providers.items()}


def label(conn: psycopg.Connection, sec_id: int, display: str, headlines: list[dict]) -> tuple[dict, str | None]:
    """Cached labels for these headlines; unlabelled ones (newest first, up to MAX_LABEL_BATCH) in one call."""
    model = llm_config.MODELS["specialist"]
    ids = [h["id"] for h in headlines]
    labels = {nid: (s, t) for nid, s, t in conn.execute(
        "SELECT news_id, sentiment, event_tag FROM news_labels WHERE security_id = %s AND model = %s AND prompt_version = %s"
        " AND news_id = ANY(%s)", (sec_id, model, PROMPT_VERSION, ids))}
    todo = sorted((h for h in headlines if h["id"] not in labels), key=lambda h: h["published_at"], reverse=True)[:MAX_LABEL_BATCH]
    if not todo:
        return labels, None
    try:
        result = call_json("specialist", SYSTEM, prompt(display, [(i, h["headline"]) for i, h in enumerate(todo)]), NewsLabels).value
    except LLMError as e:
        return labels, str(e)
    for lab in result.labels:
        if 0 <= lab.index < len(todo) and todo[lab.index]["id"] not in labels:
            nid = todo[lab.index]["id"]
            labels[nid] = (lab.sentiment, lab.event_tag)
            conn.execute("INSERT INTO news_labels (news_id, security_id, model, prompt_version, sentiment, event_tag)"
                         " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                         (nid, sec_id, model, PROMPT_VERSION, lab.sentiment, lab.event_tag))
    return labels, None


def run(conn: psycopg.Connection, inp: M5Input) -> Envelope:
    """Match, label (cached), compute and log to agent_runs/agent_outputs; LLM cost lands on the run."""
    def go():
        sec_id, isin, symbol, display, matched, providers = load_inputs(conn, inp)
        end = datetime.combine(inp.as_of + timedelta(days=1), dtime(0), IST)
        recent = [h for h in matched if h["published_at"] >= end - timedelta(days=WINDOW_DAYS)]
        labels, error = label(conn, sec_id, display, recent)
        return compute(isin, symbol, matched, labels, error, inp.as_of, datetime.now(IST)), providers

    return run_logged(conn, AGENT_ID, inp.subject, datetime.combine(inp.as_of, dtime(0), IST), go)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subject", help="NSE symbol or ISIN")
    ap.add_argument("--alias", action="append", default=[])
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        print(run(conn, M5Input(subject=args.subject, as_of=args.as_of, aliases=args.alias)).model_dump_json(indent=2))
