"""M3 Forensic red flags: compute layer only (no LLM). Financials + shareholding + corporate events -> envelope.

Seven rule-based checks, each with its inputs shown. The red-flag score (0-100) is the sum of the weights of
the checks that triggered, always reported with the weight that could be evaluated, so missing data never reads
as a clean record. Describes only; it is not a rating or a recommendation.
Usage: uv run python -m services.agents.desks.markets.m3_forensics RELIANCE [--as-of 2026-10-27]
"""

import argparse
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.desks.markets.m1_technicals import CA_RATIOS, CA_TOLERANCE
from services.agents.desks.markets.m2_fundamentals import fy_label, load_inputs as load_financials, pick_statements, years_back
from services.agents.envelope import Confidence, Envelope, Flag, Gap, Metric, Source
from services.agents.runlog import run_logged

AGENT_ID = "M3"
IST = ZoneInfo("Asia/Kolkata")
# red_flags_v1: name -> (weight, description). Weights sum to 100.
# ponytail: fixed v1 thresholds and weights; review with an analyst and per sector (banks, holding companies).
CHECKS = {
    "pledge_level": (15, "promoter pledge is at least 20% of promoter shares"),
    "pledge_rise": (15, "promoter pledge rose by at least 5 percentage points in a year"),
    "promoter_stake_fall": (10, "promoter holding fell by at least 5 percentage points in 3 years"),
    "auditor_resignation": (20, "an auditor resigned in the last 3 years"),
    "cash_gap": (20, "3-year operating cash flow is below half of 3-year profit"),
    "contingent_liabilities": (10, "contingent liabilities exceed half of net worth"),
    "dilution": (10, "share count grew more than 10% in 3 years, excluding split/bonus steps"),
}
PLEDGE_LEVEL, PLEDGE_RISE, PROMOTER_FALL = 20.0, 5.0, 5.0  # percentage points
CASH_GAP, CONTINGENT, DILUTION = 0.5, 0.5, 0.10
SHAREHOLDING_STALE_DAYS = 200  # latest quarter more than ~2 quarters old
EVENTS_STALE_DAYS = 45  # event coverage must reach to within this of as_of
# share-count ratios a split/bonus produces (inverse of the price ratios M1 detects)
SPLIT_BONUS_SHARE_RATIOS = sorted({1 / r for r in CA_RATIOS})


class M3Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: str = Field(min_length=1)  # NSE symbol or ISIN
    as_of: date


# --- formulas -------------------------------------------------------------------------------------------

def share_dilution(counts: list[float]) -> tuple[float, int]:
    """dilution_3y: growth of share count across consecutive year-ends, multiplying only the year-on-year ratios
    that are not split/bonus ratios. Returns (dilution, steps excluded as split/bonus)."""
    growth, excluded = 1.0, 0
    for a, b in zip(counts, counts[1:]):
        r = b / a
        if any(abs(r / s - 1) <= CA_TOLERANCE for s in SPLIT_BONUS_SHARE_RATIOS):
            excluded += 1
        else:
            growth *= r
    return growth - 1, excluded


def red_flag_score(results: dict[str, bool]) -> tuple[float, float]:
    """red_flags_v1: (sum of weights of triggered checks, sum of weights of evaluated checks)."""
    return (float(sum(CHECKS[k][0] for k, hit in results.items() if hit)),
            float(sum(CHECKS[k][0] for k in results)))


# --- envelope -------------------------------------------------------------------------------------------

def compute(isin: str, symbol: str | None, fin_rows: list[tuple], shareholding: list[tuple], events: list[tuple],
            covered_until: date | None, as_of: date, retrieved_at: datetime) -> Envelope:
    """shareholding: (quarter_end, promoter_pct, pledged_pct_of_promoter); events: (event_date, kind, detail)."""
    subject = symbol or isin
    metrics: list[Metric] = []
    flags: list[Flag] = []
    gaps: list[Gap] = [Gap(item="related-party transactions", reason="need annual-report notes (text); not covered yet")]
    results: dict[str, bool] = {}

    def add(mid: str, name: str, value: float | str, formula: str, source: str, unit: str | None = None):
        metrics.append(Metric(id=mid, name=name, value=value, unit=unit, formula_id=formula, source_query_id=source))

    # shareholding checks
    sh = sorted(r for r in shareholding if r[0] <= as_of)
    if not sh or (as_of - sh[-1][0]).days > SHAREHOLDING_STALE_DAYS:
        for k in ("pledge_level", "pledge_rise", "promoter_stake_fall"):
            gaps.append(Gap(item=k, reason=f"no shareholding pattern within {SHAREHOLDING_STALE_DAYS} days of {as_of}"))
    else:
        by_q = {q: (p, pl) for q, p, pl in sh}
        q, promoter, pledged = sh[-1]
        add("promoter_pct", f"Promoter holding, quarter ended {q}", promoter, "item:promoter_pct", "shareholding", "%")
        add("promoter_pledged_pct", f"Pledged share of promoter holding, quarter ended {q}", pledged,
            "item:promoter_pledged_pct", "shareholding", "%")
        results["pledge_level"] = pledged >= PLEDGE_LEVEL
        if (year_ago := by_q.get(years_back(q, 1))) is not None:
            change = pledged - year_ago[1]
            add("pledge_change_1y", "Change in pledged share of promoter holding over a year", change, "diff_1y", "shareholding", "pp")
            results["pledge_rise"] = change >= PLEDGE_RISE
        else:
            gaps.append(Gap(item="pledge_rise", reason=f"no shareholding for {years_back(q, 1)}"))
        if (three := by_q.get(years_back(q, 3))) is not None:
            change = promoter - three[0]
            add("promoter_change_3y", "Change in promoter holding over 3 years", change, "diff_3y", "shareholding", "pp")
            results["promoter_stake_fall"] = change <= -PROMOTER_FALL
        else:
            gaps.append(Gap(item="promoter_stake_fall", reason=f"no shareholding for {years_back(q, 3)}"))

    # auditor events (only meaningful with coverage)
    if covered_until is None or (as_of - covered_until).days > EVENTS_STALE_DAYS:
        gaps.append(Gap(item="auditor_resignation", reason=f"corporate events not covered up to {as_of}"))
    else:
        since = years_back(as_of, 3)
        resignations = [e for e in events if e[1] == "auditor_resignation" and since < e[0] <= as_of]
        add("auditor_resignations_3y", "Auditor resignations in the last 3 years", float(len(resignations)), "count_3y", "events")
        results["auditor_resignation"] = bool(resignations)

    # financial checks
    picked = pick_statements(fin_rows, as_of)
    if picked is None:
        for k in ("cash_gap", "contingent_liabilities", "dilution"):
            gaps.append(Gap(item=k, reason="no annual financials"))
    else:
        _, annual, _, fy = picked
        last3 = [annual.get(years_back(fy, n), {}) for n in (2, 1, 0)]
        if all("cfo" in p and "pat" in p for p in last3) and sum(p["pat"] for p in last3) > 0:
            cc = sum(p["cfo"] for p in last3) / sum(p["pat"] for p in last3)
            add("cash_conversion_3y", f"3-year CFO / PAT to {fy_label(fy)}", cc, "cash_conversion_3y", "financials", "ratio")
            results["cash_gap"] = cc < CASH_GAP
        else:
            gaps.append(Gap(item="cash_gap", reason="needs cfo and pat for 3 years with positive total pat"))
        cur = annual[fy]
        if "contingent_liabilities" in cur and cur.get("equity_owners", 0) > 0:
            ratio = cur["contingent_liabilities"] / cur["equity_owners"]
            add("contingent_to_networth", f"Contingent liabilities / net worth, {fy_label(fy)}", ratio,
                "contingent_to_networth", "financials", "ratio")
            results["contingent_liabilities"] = ratio > CONTINGENT
        else:
            gaps.append(Gap(item="contingent_liabilities", reason="needs contingent_liabilities and positive equity_owners"))
        counts = [annual.get(years_back(fy, n), {}).get("shares_outstanding") for n in (3, 2, 1, 0)]
        if all(counts):
            dil, excluded = share_dilution(counts)
            add("dilution_3y", f"Share count growth over 3 years to {fy_label(fy)}, excluding split/bonus steps", dil,
                "dilution_3y", "financials", "ratio")
            if excluded:
                add("split_bonus_steps", "Year-on-year share count changes treated as split/bonus", float(excluded),
                    "dilution_3y", "financials")
            results["dilution"] = dil > DILUTION
        else:
            gaps.append(Gap(item="dilution", reason="needs shares_outstanding at 4 consecutive year-ends"))

    for k, hit in results.items():
        weight, desc = CHECKS[k]
        add(f"check_{k}", f"{desc} ({weight} points)", "triggered" if hit else "clear", "red_flags_v1", "checks")
        if hit:
            flags.append(Flag(severity="alert" if k in ("auditor_resignation", "cash_gap") else "watch", reason=desc,
                              metric_ids=[f"check_{k}"]))
    score, evaluated = red_flag_score(results)
    add("red_flag_score", "Red-flag score (sum of triggered check weights)", score, "red_flags_v1", "checks", "points")
    add("evaluated_weight", "Check weight that could be evaluated with available data", evaluated, "red_flags_v1", "checks", "points")

    if evaluated < 50:
        confidence = Confidence(level="low", reason=f"only {evaluated:.0f} of 100 points of checks evaluated")
    else:
        confidence = Confidence(level="medium", reason=f"{evaluated:.0f} of 100 points evaluated; related-party notes not covered")
    refs = {"financials": f"fundamentals_quarterly isin={isin} ..{as_of}",
            "shareholding": f"shareholding isin={isin} ..{as_of}",
            "events": f"corporate_events isin={isin} covered until {covered_until}",
            "checks": f"red_flags_v1 over financials, shareholding and events for {isin}"}
    used = {m.source_query_id for m in metrics}
    return Envelope(agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(as_of, dtime(0), IST), metrics=metrics,
                    findings=[], flags=flags, confidence=confidence, gaps=gaps,
                    sources=[Source(id=s, kind="dataset", ref=r, retrieved_at=retrieved_at) for s, r in refs.items() if s in used])


# --- run ------------------------------------------------------------------------------------------------

def load_inputs(conn: psycopg.Connection, subject: str, as_of: date):
    """(isin, symbol, financial rows, shareholding, events, covered_until, providers)."""
    isin, symbol, fin_rows, fin_prov = load_financials(conn, subject, as_of)
    (sid,) = conn.execute("SELECT id FROM securities WHERE isin = %s", (isin,)).fetchone()
    sh_src, ev_src = set(), set()
    shareholding = []
    for q, p, pl, src in conn.execute("SELECT quarter_end, promoter_pct, promoter_pledged_pct, source FROM shareholding"
                                      " WHERE security_id = %s AND quarter_end <= %s", (sid, as_of)):
        shareholding.append((q, float(p), float(pl)))
        sh_src.add(src)
    events = []
    for d, kind, detail, src in conn.execute("SELECT event_date, kind, detail, source FROM corporate_events"
                                             " WHERE security_id = %s AND event_date > %s AND event_date <= %s",
                                             (sid, as_of - timedelta(days=4 * 366), as_of)):
        events.append((d, kind, detail))
        ev_src.add(src)
    cov = conn.execute("SELECT covered_until, source FROM corporate_events_coverage WHERE security_id = %s", (sid,)).fetchone()
    covered_until = None
    if cov:
        covered_until = min(cov[0], as_of)
        ev_src.add(cov[1])
    providers = {"financials": fin_prov["financials"], "shareholding": sorted(sh_src), "events": sorted(ev_src),
                 "checks": sorted(set(fin_prov["financials"]) | sh_src | ev_src)}
    return isin, symbol, fin_rows, shareholding, events, covered_until, providers


def run(conn: psycopg.Connection, inp: M3Input) -> Envelope:
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
        print(run(conn, M3Input(subject=args.subject, as_of=args.as_of)).model_dump_json(indent=2))
