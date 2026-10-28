"""M6 Peers and sector: compute layer only (no LLM). Classification + M2/M7 caches -> standard envelope.

Ranks a company against its industry peers (sector when the industry has too few members) on growth, margins,
returns, leverage and valuation. Reads the latest cached M2 and M7 envelopes from agent_outputs; it never runs
other agents (CLAUDE.md rule 2). Percentiles describe position only; higher is not "better" for every metric.
Usage: uv run python -m services.agents.desks.markets.m6_peers RELIANCE [--as-of 2026-10-27]
"""

import argparse
import statistics
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.envelope import Confidence, Envelope, Flag, Gap, Metric, Source
from services.agents.runlog import run_logged

AGENT_ID = "M6"
IST = ZoneInfo("Asia/Kolkata")
MIN_PEERS = 3
MAX_PEERS = 50
VALUATION_STALE_DAYS = 7  # M7 envelopes older than this before as_of are ignored (prices move daily)
# metric id in the cached envelope -> (cache, label)
RANKED = {
    "revenue_cagr_3y": ("M2", "revenue CAGR, 3 years"), "revenue_cagr_5y": ("M2", "revenue CAGR, 5 years"),
    "pat_cagr_3y": ("M2", "PAT CAGR, 3 years"), "pat_cagr_5y": ("M2", "PAT CAGR, 5 years"),
    "revenue_ttm_yoy": ("M2", "TTM revenue growth"), "ebitda_margin": ("M2", "EBITDA margin"),
    "pat_margin": ("M2", "PAT margin"), "roe": ("M2", "ROE"), "roce": ("M2", "ROCE"),
    "debt_equity": ("M2", "debt / equity"),
    "pe": ("M7", "P/E"), "pb": ("M7", "P/B"), "ev_ebitda": ("M7", "EV/EBITDA"),
}
CACHE_SOURCE = {"M2": "peer_m2", "M7": "peer_m7"}


class M6Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: str = Field(min_length=1)  # NSE symbol or ISIN
    as_of: date


# --- formulas -------------------------------------------------------------------------------------------

def percentile(value: float, peers: list[float]) -> float:
    """peer_percentile: share of peers with a strictly lower value (0 = lowest in the group, 1 = highest)."""
    return sum(p < value for p in peers) / len(peers)


def numeric_metrics(env: Envelope | None) -> dict[str, float]:
    return {m.id: m.value for m in env.metrics if isinstance(m.value, float | int)} if env else {}


# --- envelope -------------------------------------------------------------------------------------------

def compute(subject: str, isin: str, group: tuple[str, str] | None, caches: dict[str, dict[str, Envelope]],
            as_of: date, retrieved_at: datetime) -> Envelope:
    """group: (level 'industry'|'sector', name) or None. caches: {'M2'|'M7': {security key: envelope}}, keyed by
    the same key as `subject` for the company itself and by symbol/ISIN for peers."""
    metrics: list[Metric] = []
    flags: list[Flag] = []
    gaps: list[Gap] = []

    def envelope(confidence: Confidence) -> Envelope:
        refs = {"classification": f"securities sector/industry for {isin}",
                "peer_m2": f"latest M2 outputs for the {group[0] if group else ''} peer group on or before {as_of}",
                "peer_m7": f"latest M7 outputs within {VALUATION_STALE_DAYS} days of {as_of}"}
        used = {m.source_query_id for m in metrics}
        return Envelope(agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(as_of, dtime(0), IST), metrics=metrics,
                        findings=[], flags=flags, confidence=confidence, gaps=gaps,
                        sources=[Source(id=s, kind="dataset", ref=r, retrieved_at=retrieved_at) for s, r in refs.items() if s in used])

    if group is None:
        gaps.append(Gap(item="peer group", reason=f"no sector/industry classification for {isin}"))
        return envelope(Confidence(level="low", reason="not classified"))
    level, name = group
    metrics.append(Metric(id="peer_group", name="Peer group", value=f"{level}: {name}", unit=None,
                          formula_id="peer_group", source_query_id="classification"))
    if level == "sector":
        gaps.append(Gap(item="industry peers", reason=f"fewer than {MIN_PEERS} classified peers in the industry; sector used"))

    own = {cache: numeric_metrics(envs.get(subject)) for cache, envs in caches.items()}
    if not own.get("M2"):
        gaps.append(Gap(item="company fundamentals", reason="no M2 output for the company on or before as_of"))
    peer_vals = {cache: [numeric_metrics(e) for k, e in envs.items() if k != subject] for cache, envs in caches.items()}

    for mid, (cache, label) in RANKED.items():
        if mid not in own.get(cache, {}):
            gaps.append(Gap(item=f"{label} rank", reason=f"company has no {mid} in its {cache} output"))
            continue
        values = [p[mid] for p in peer_vals.get(cache, []) if mid in p]
        if len(values) < MIN_PEERS:
            gaps.append(Gap(item=f"{label} rank", reason=f"{len(values)} peers with {mid}, needs {MIN_PEERS}"))
            continue
        v, src = own[cache][mid], CACHE_SOURCE[cache]
        pct = percentile(v, values)
        metrics += [
            Metric(id=mid, name=f"Company {label}", value=v, unit="ratio" if cache == "M2" else "x", formula_id=f"cache:{cache}", source_query_id=src),
            Metric(id=f"{mid}_peer_median", name=f"Peer median {label}", value=statistics.median(values), unit="ratio" if cache == "M2" else "x",
                   formula_id="peer_median", source_query_id=src),
            Metric(id=f"{mid}_percentile", name=f"Share of peers with a lower {label}", value=pct, unit="ratio",
                   formula_id="peer_percentile", source_query_id=src),
            Metric(id=f"{mid}_peer_count", name=f"Peers with {label}", value=float(len(values)), unit=None,
                   formula_id="peer_percentile", source_query_id=src),
        ]
        if pct in (0.0, 1.0):
            flags.append(Flag(severity="info", reason=f"{label} is the {'highest' if pct == 1 else 'lowest'} among "
                                                      f"{len(values) + 1} companies in the {level}", metric_ids=[mid, f"{mid}_percentile"]))

    ranked = sum(m.id.endswith("_percentile") for m in metrics)
    if not own.get("M2") or ranked == 0:
        confidence = Confidence(level="low", reason="no metric could be ranked")
    elif gaps:
        confidence = Confidence(level="medium", reason=f"{ranked} of {len(RANKED)} metrics ranked; {len(gaps)} gap(s)")
    else:
        confidence = Confidence(level="high", reason=f"all {len(RANKED)} metrics ranked against {level} peers")
    return envelope(confidence)


# --- run ------------------------------------------------------------------------------------------------

def latest_outputs(conn: psycopg.Connection, agent_id: str, keys: list[str], as_of: date,
                   min_as_of: date | None = None) -> tuple[dict[str, Envelope], set[str]]:
    """Per subject key, the ok envelope of agent_id (run for a date on or before as_of) with the newest data
    (envelope as_of), newest run on ties; if min_as_of is given, only data from that day on. Plus their providers.
    Ordering by data date means a later backfill run for an older date never hides a current reading."""
    cutoff = datetime.combine(as_of + timedelta(days=1), dtime(0), IST)
    floor = datetime.combine(min_as_of, dtime(0), IST) if min_as_of else datetime.min.replace(tzinfo=IST)
    out, providers = {}, set()
    for subj, env in conn.execute(
        """SELECT DISTINCT ON (o.envelope->>'subject') o.envelope->>'subject', o.envelope
           FROM agent_outputs o JOIN agent_runs r ON r.id = o.run_id
           WHERE r.agent_id = %s AND r.status = 'ok' AND o.envelope->>'subject' = ANY(%s) AND r.as_of < %s
             AND (o.envelope->>'as_of')::timestamptz >= %s
           ORDER BY o.envelope->>'subject', (o.envelope->>'as_of')::timestamptz DESC, r.id DESC""",
        (agent_id, keys, cutoff, floor)):
        e = Envelope.model_validate(env)
        out[subj] = e
        providers |= {p for s in e.sources for p in s.providers}
    return out, providers


def load_inputs(conn: psycopg.Connection, subject: str, as_of: date):
    """(subject key, isin, group, caches, providers)."""
    row = conn.execute("SELECT isin, nse_symbol, sector, industry, classification_source FROM securities"
                       " WHERE nse_symbol = %s OR isin = %s", (subject, subject)).fetchone()
    if row is None:
        raise LookupError(f"unknown security {subject!r}")
    isin, symbol, sector, industry, cls_src = row
    key = symbol or isin
    group, members = None, []
    for level, name in (("industry", industry), ("sector", sector)):
        if name is None:
            continue
        members = [s or i for i, s in conn.execute(
            f"SELECT isin, nse_symbol FROM securities WHERE {level} = %s AND isin <> %s AND nse_symbol IS NOT NULL"
            " ORDER BY isin LIMIT %s", (name, isin, MAX_PEERS))]
        if len(members) >= MIN_PEERS:
            group = (level, name)
            break
    caches, providers = {}, {"classification": [cls_src] if cls_src else []}
    if group:
        for cache in ("M2", "M7"):
            min_as_of = as_of - timedelta(days=VALUATION_STALE_DAYS) if cache == "M7" else None
            caches[cache], prov = latest_outputs(conn, cache, [key, *members], as_of, min_as_of)
            providers[CACHE_SOURCE[cache]] = sorted(prov)
    return key, isin, group, caches, providers


def run(conn: psycopg.Connection, inp: M6Input) -> Envelope:
    """Compute and log to agent_runs/agent_outputs; failures are logged and re-raised."""
    def go():
        *inputs, providers = load_inputs(conn, inp.subject, inp.as_of)
        return compute(*inputs, inp.as_of, datetime.now(IST)), providers

    return run_logged(conn, AGENT_ID, inp.subject, datetime.combine(inp.as_of, dtime(0), IST), go)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subject", help="NSE symbol or ISIN")
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        print(run(conn, M6Input(subject=args.subject, as_of=args.as_of)).model_dump_json(indent=2))
