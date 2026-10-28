"""M2 Fundamentals: compute layer only (no LLM). fundamentals_quarterly -> standard envelope.

Growth (revenue/PAT CAGR, TTM YoY), margins, ROE, ROCE, leverage, cash conversion, FCF and a transparent
rule-based growth-quality score. Line items: services/pipelines/fundamentals.ITEMS.
Usage: uv run python -m services.agents.desks.markets.m2_fundamentals RELIANCE [--as-of 2026-10-27]
"""

import argparse
from collections import defaultdict
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.envelope import Confidence, Envelope, Flag, Gap, Metric, Source
from services.agents.runlog import Providers, run_logged

AGENT_ID = "M2"
IST = ZoneInfo("Asia/Kolkata")
CAGR_YEARS = (3, 5, 10)
STALE_DAYS = 456  # latest annual period older than ~15 months: a year's results are missing
MIN_YEARS_CONFIDENT = 6
# growth_quality_v1: each check is shown with its result; the score is the share passed.
# ponytail: fixed thresholds chosen for v1; revisit per sector (e.g. capital-heavy industries) before launch.
QUALITY_RULES = {
    "revenue_up_years": "revenue grew in at least 4 of the last 5 years",
    "cash_conversion": "3-year CFO / PAT at least 0.8",
    "roce": "ROCE at least 12%",
    "leverage": "debt / equity at most 1.0",
    "profit_tracks_revenue": "5-year PAT CAGR within 10 percentage points of revenue CAGR",
}

Statement = dict[date, dict[str, float]]  # period_end -> item -> value


class M2Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: str = Field(min_length=1)  # NSE symbol or ISIN
    as_of: date


# --- formulas -------------------------------------------------------------------------------------------

def years_back(d: date, n: int) -> date:
    return d.replace(year=d.year - n) if not (d.month == 2 and d.day == 29) else date(d.year - n, 2, 28)


def cagr(start: float, end: float, years: int) -> float | None:
    """cagr_n: (end / start) ** (1 / years) - 1; undefined (None) unless both are positive."""
    return (end / start) ** (1 / years) - 1 if start > 0 and end > 0 else None


def ebitda(p: dict[str, float]) -> float | None:
    """ebitda: PBT - exceptional items + finance cost + depreciation - other income (operating EBITDA)."""
    keys = ("pbt", "exceptional_items", "finance_cost", "depreciation", "other_income")
    if any(k not in p for k in keys):
        return None
    return p["pbt"] - p["exceptional_items"] + p["finance_cost"] + p["depreciation"] - p["other_income"]


def ebit(p: dict[str, float]) -> float | None:
    """ebit: PBT - exceptional items + finance cost."""
    if any(k not in p for k in ("pbt", "exceptional_items", "finance_cost")):
        return None
    return p["pbt"] - p["exceptional_items"] + p["finance_cost"]


def debt(p: dict[str, float]) -> float | None:
    if "borrowings_current" not in p or "borrowings_noncurrent" not in p:
        return None
    return p["borrowings_current"] + p["borrowings_noncurrent"]


def capital_employed(p: dict[str, float]) -> float | None:
    """capital_employed: total equity + all borrowings."""
    d = debt(p)
    return p["equity_total"] + d if d is not None and "equity_total" in p else None


def avg_return(numerator: float | None, base_now: float | None, base_prev: float | None) -> float | None:
    """Return on the average of opening and closing base (ROE: equity; ROCE: capital employed)."""
    if numerator is None or base_now is None or base_prev is None or (base_now + base_prev) <= 0:
        return None
    return numerator / ((base_now + base_prev) / 2)


def ttm_yoy(quarters: Statement, item: str) -> tuple[float, float, float] | None:
    """ttm_yoy: sum of the last 4 quarters vs the 4 before; needs 8 consecutive quarter-ends.
    Returns (ttm, previous ttm, growth); growth None-guarded by a positive base."""
    ends = sorted(d for d, v in quarters.items() if item in v)[-8:]
    if len(ends) < 8 or any(_months_between(a, b) != 3 for a, b in zip(ends, ends[1:])):
        return None
    prev, now = sum(quarters[d][item] for d in ends[:4]), sum(quarters[d][item] for d in ends[4:])
    return (now, prev, now / prev - 1) if prev > 0 else None


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def revenue_up_years(annual: Statement, ends: list[date], years: int = 5) -> int | None:
    """Count of the last `years` years whose revenue exceeded the year before."""
    revs = [annual[d]["revenue_ops"] for d in ends if "revenue_ops" in annual[d]][-(years + 1):]
    if len(revs) < years + 1:
        return None
    return sum(b > a for a, b in zip(revs, revs[1:]))


def fy_label(d: date) -> str:
    return f"FY{d.year % 100:02d}"


# --- envelope -------------------------------------------------------------------------------------------

def split(rows: list[tuple]) -> dict[bool, dict[str, Statement]]:
    """rows (period_end, period_type, consolidated, item, value) -> {consolidated: {'A'|'Q': statement}}."""
    out: dict[bool, dict[str, Statement]] = {True: {"A": defaultdict(dict), "Q": defaultdict(dict)},
                                              False: {"A": defaultdict(dict), "Q": defaultdict(dict)}}
    for pe, pt, cons, item, value in rows:
        out[cons][pt][pe][item] = value
    return out


def pick_statements(rows: list[tuple], as_of: date) -> tuple[bool, Statement, Statement, date] | None:
    """(consolidated?, annual, quarterly, latest FY end) for the basis with the latest annual revenue on or before
    as_of; consolidated wins ties. None if neither basis has annual revenue."""
    stmts = split([r for r in rows if r[0] <= as_of])
    latest = {c: max((d for d, v in stmts[c]["A"].items() if "revenue_ops" in v), default=None) for c in (True, False)}
    if latest[True] is None and latest[False] is None:
        return None
    consolidated = latest[True] is not None and (latest[False] is None or latest[True] >= latest[False])
    return consolidated, stmts[consolidated]["A"], stmts[consolidated]["Q"], latest[consolidated]


def compute(isin: str, symbol: str | None, rows: list[tuple], as_of: date, retrieved_at: datetime) -> Envelope:
    subject = symbol or isin
    metrics: list[Metric] = []
    flags: list[Flag] = []
    gaps: list[Gap] = []

    def add(mid: str, name: str, value: float | str, formula: str, unit: str | None = "ratio"):
        metrics.append(Metric(id=mid, name=name, value=value, unit=unit, formula_id=formula, source_query_id="financials"))

    picked = pick_statements(rows, as_of)
    if picked is None:
        return Envelope(agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(as_of, dtime(0), IST), metrics=[], findings=[],
                        flags=[], sources=[], confidence=Confidence(level="low", reason="no annual revenue in financials"),
                        gaps=[Gap(item="financials", reason=f"no annual revenue_ops for {isin} on or before {as_of} "
                                                            "(banks/NBFCs/insurers are not covered by the current line items)")])
    consolidated, annual, quarters, fy = picked
    basis = "consolidated" if consolidated else "standalone"
    if not consolidated:
        gaps.append(Gap(item="consolidated statements", reason="not available for the latest year; standalone used"))
        flags.append(Flag(severity="info", reason="figures are standalone, not consolidated"))
    ends = sorted(annual)
    cur = annual[fy]
    prev = annual.get(years_back(fy, 1), {})
    label = fy_label(fy)

    add("revenue", f"Revenue from operations, {label}", cur["revenue_ops"], "item:revenue_ops", "INR cr")
    if "pat_owners" in cur:
        add("pat", f"Profit attributable to owners, {label}", cur["pat_owners"], "item:pat_owners", "INR cr")
        if cur["pat_owners"] < 0:
            flags.append(Flag(severity="watch", reason=f"loss attributable to owners in {label}", metric_ids=["pat"]))

    # growth
    for item, short in (("revenue_ops", "revenue"), ("pat_owners", "pat")):
        for n in CAGR_YEARS:
            base = annual.get(years_back(fy, n), {}).get(item)
            if base is None or item not in cur:
                gaps.append(Gap(item=f"{short} {n}-year CAGR", reason=f"no {item} for {fy_label(years_back(fy, n))}"))
            elif (g := cagr(base, cur[item], n)) is None:
                gaps.append(Gap(item=f"{short} {n}-year CAGR", reason="start or end value not positive"))
            else:
                add(f"{short}_cagr_{n}y", f"{short.title()} CAGR, {n} years to {label}", g, f"cagr_{n}")
        if (t := ttm_yoy(quarters, item)) is None:
            gaps.append(Gap(item=f"{short} TTM growth", reason="needs 8 consecutive quarters with a positive base"))
        else:
            add(f"{short}_ttm", f"{short.title()}, trailing 12 months", t[0], "ttm_yoy", "INR cr")
            add(f"{short}_ttm_yoy", f"{short.title()} TTM growth vs prior 12 months", t[2], "ttm_yoy")

    # margins
    if (e := ebitda(cur)) is not None and cur["revenue_ops"] > 0:
        add("ebitda", f"EBITDA, {label}", e, "ebitda", "INR cr")
        add("ebitda_margin", f"EBITDA margin, {label}", e / cur["revenue_ops"], "ebitda_margin")
    else:
        gaps.append(Gap(item="EBITDA margin", reason="needs pbt, exceptional_items, finance_cost, depreciation, other_income"))
    if "pat" in cur and cur["revenue_ops"] > 0:
        add("pat_margin", f"PAT margin, {label}", cur["pat"] / cur["revenue_ops"], "pat_margin")
    else:
        gaps.append(Gap(item="PAT margin", reason="needs pat"))

    # returns on capital
    roe = avg_return(cur.get("pat_owners"), cur.get("equity_owners"), prev.get("equity_owners"))
    if roe is not None:
        add("roe", f"ROE (on average equity), {label}", roe, "roe_avg")
    else:
        gaps.append(Gap(item="ROE", reason="needs pat_owners and equity_owners for this and the previous year"))
    roce = avg_return(ebit(cur), capital_employed(cur), capital_employed(prev))
    if roce is not None:
        add("roce", f"ROCE (on average capital employed), {label}", roce, "roce_avg")
    else:
        gaps.append(Gap(item="ROCE", reason="needs EBIT inputs, equity_total and borrowings for this and the previous year"))

    # leverage
    de = None
    if (d := debt(cur)) is not None and "equity_total" in cur:
        if cur["equity_total"] <= 0:
            flags.append(Flag(severity="alert", reason=f"total equity is not positive in {label}"))
            gaps.append(Gap(item="debt / equity", reason="equity not positive"))
        else:
            de = d / cur["equity_total"]
            add("debt_equity", f"Debt / equity, {label}", de, "debt_equity")
            if de > 2:
                flags.append(Flag(severity="watch", reason=f"debt is {de:.1f}x equity", metric_ids=["debt_equity"]))
    else:
        gaps.append(Gap(item="debt / equity", reason="needs borrowings and equity_total"))

    # cash
    last3 = [annual[e_] for e_ in ends if e_ <= fy][-3:]
    cc = None
    if len(last3) == 3 and all("cfo" in p and "pat" in p for p in last3) and sum(p["pat"] for p in last3) > 0:
        cc = sum(p["cfo"] for p in last3) / sum(p["pat"] for p in last3)
        add("cash_conversion_3y", "3-year CFO / PAT", cc, "cash_conversion_3y")
        if cc < 0.5:
            flags.append(Flag(severity="watch", reason=f"3-year operating cash flow is {cc:.0%} of profit",
                              metric_ids=["cash_conversion_3y"]))
    else:
        gaps.append(Gap(item="cash conversion", reason="needs cfo and pat for 3 years with positive total pat"))
    if "cfo" in cur and "capex" in cur:
        add("fcf", f"Free cash flow (CFO - capex), {label}", cur["cfo"] - cur["capex"], "fcf", "INR cr")
    else:
        gaps.append(Gap(item="free cash flow", reason="needs cfo and capex"))

    # growth quality v1
    v = {m.id: m.value for m in metrics}
    checks: dict[str, bool] = {}
    if (up := revenue_up_years(annual, [e_ for e_ in ends if e_ <= fy])) is not None:
        checks["revenue_up_years"] = up >= 4
    if cc is not None:
        checks["cash_conversion"] = cc >= 0.8
    if roce is not None:
        checks["roce"] = roce >= 0.12
    if de is not None:
        checks["leverage"] = de <= 1.0
    if "revenue_cagr_5y" in v and "pat_cagr_5y" in v:
        checks["profit_tracks_revenue"] = abs(v["pat_cagr_5y"] - v["revenue_cagr_5y"]) <= 0.10
    for name, passed in checks.items():
        add(f"quality_{name}", QUALITY_RULES[name], "pass" if passed else "fail", "growth_quality_v1", None)
    if len(checks) >= 3:
        add("quality_score", f"Growth quality: share of {len(checks)} checks passed", sum(checks.values()) / len(checks),
            "growth_quality_v1")
    else:
        gaps.append(Gap(item="growth quality score", reason=f"only {len(checks)} of {len(QUALITY_RULES)} checks computable"))

    stale = (as_of - fy).days > STALE_DAYS
    if stale:
        flags.append(Flag(severity="watch", reason=f"latest annual results are for {label} ({fy})"))
    years = len([e_ for e_ in ends if e_ <= fy and "revenue_ops" in annual[e_]])
    if stale or years < 3:
        confidence = Confidence(level="low", reason="stale financials" if stale else f"only {years} years of financials")
    elif gaps or years < MIN_YEARS_CONFIDENT:
        confidence = Confidence(level="medium", reason=f"{years} years of {basis} financials; {len(gaps)} gap(s)")
    else:
        confidence = Confidence(level="high", reason=f"{years} years of {basis} financials, no gaps")

    first = min(e_ for e_ in ends if "revenue_ops" in annual[e_])
    source = Source(id="financials", kind="dataset", retrieved_at=retrieved_at,
                    ref=f"fundamentals_quarterly isin={isin} {basis} {fy_label(first)}..{label}")
    return Envelope(agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(fy, dtime(0), IST), metrics=metrics,
                    findings=[], flags=flags, sources=[source], confidence=confidence, gaps=gaps)


# --- run ------------------------------------------------------------------------------------------------

def load_inputs(conn: psycopg.Connection, subject: str, as_of: date) -> tuple[str, str | None, list[tuple], Providers]:
    """(isin, symbol, rows (period_end, period_type, consolidated, item, value), providers)."""
    row = conn.execute("SELECT id, isin, nse_symbol FROM securities WHERE nse_symbol = %s OR isin = %s", (subject, subject)).fetchone()
    if row is None:
        raise LookupError(f"unknown security {subject!r}")
    sid, isin, symbol = row
    data = conn.execute(
        "SELECT period_end, period_type, consolidated, item, value, source FROM fundamentals_quarterly"
        " WHERE security_id = %s AND period_end <= %s", (sid, as_of)).fetchall()
    rows = [(pe, pt, c, item, float(v)) for pe, pt, c, item, v, _ in data]
    return isin, symbol, rows, {"financials": sorted({r[5] for r in data})}


def run(conn: psycopg.Connection, inp: M2Input) -> Envelope:
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
        print(run(conn, M2Input(subject=args.subject, as_of=args.as_of)).model_dump_json(indent=2))
