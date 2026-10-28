"""MF3 Closet indexer detector: compute layer only (no LLM). Portfolio + benchmark constituents + NAV -> envelope.

Active share vs the category's benchmark, tracking error, expense ratio per unit of active share, and a
descriptive closet-indexer flag. Describes only: never says switch or exit (SPEC output rule).
Usage: uv run python -m services.agents.desks.funds.mf3_closet_index 122639 [--as-of 2026-10-27]
"""

import argparse
import math
import statistics
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.desks.funds.categories import BENCHMARK, is_growth, sebi_category
from services.agents.desks.funds.mf2_manager import Series, month_ends, monthly_returns
from services.agents.envelope import Confidence, Envelope, Flag, Gap, Metric, Source
from services.agents.runlog import run_logged

AGENT_ID = "MF3"
IST = ZoneInfo("Asia/Kolkata")
NAV_TIME = dtime(23, 0)
TE_MONTHS = 36
MIN_TE_MONTHS = 24
MAX_PORTFOLIO_AGE_DAYS = 75  # a disclosure month older than ~2 months before as_of is stale
MAX_CONSTITUENT_LAG_DAYS = 31  # benchmark weights must be from the portfolio month or the one before
INDEX_WEIGHT_TOLERANCE = 2.0  # benchmark weights must sum to 100 +- this, then are normalised
# Closet-indexer profile (Cremers & Petajisto style), v1 thresholds:
# ponytail: fixed cut-offs; tune per category once real portfolios are loaded (mid/small-cap indices are broader).
CLOSET_MAX_ACTIVE_SHARE = 0.60
CLOSET_MAX_TRACKING_ERROR = 0.04


class MF3Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    amfi_code: str = Field(pattern=r"^\d+$")
    as_of: date


# --- formulas -------------------------------------------------------------------------------------------

def active_share(fund: dict[str, float], index: dict[str, float]) -> float:
    """active_share: 1/2 x sum |w_fund - w_index| over every stock in either, as a fraction. Weights are % of net
    assets; index weights are normalised to 100 and the fund's uninvested remainder (100 - sum, if positive)
    counts as one off-index position (cash)."""
    total = sum(index.values())
    idx = {k: v * 100 / total for k, v in index.items()}
    diff = sum(abs(fund.get(k, 0.0) - idx.get(k, 0.0)) for k in fund.keys() | idx.keys())
    return (diff + max(0.0, 100 - sum(fund.values()))) / 200


def tracking_error(diffs: list[float]) -> float:
    """tracking_error_n: sample standard deviation of monthly (fund - benchmark) returns x sqrt(12)."""
    return statistics.stdev(diffs) * math.sqrt(12)


def active_fee(ter_pct: float, share: float) -> float:
    """active_fee: expense ratio paid per unit of active share (TER / active share), as a yearly fraction."""
    return ter_pct / 100 / share


# --- envelope -------------------------------------------------------------------------------------------

def compute(scheme: dict, holdings: dict[str, float], holdings_month: date | None, index: dict[str, float],
            index_month: date | None, ter: tuple[float, date] | None, fund_nav: Series, bench: Series,
            as_of: date, retrieved_at: datetime) -> Envelope:
    code = scheme["amfi_code"]
    category = sebi_category(scheme["category"])
    bench_name = BENCHMARK.get(category) if category else None
    metrics: list[Metric] = []
    flags: list[Flag] = []
    gaps: list[Gap] = []

    def add(mid: str, name: str, value: float | str, formula: str, source: str, unit: str | None = "ratio"):
        metrics.append(Metric(id=mid, name=name, value=value, unit=unit, formula_id=formula, source_query_id=source))

    refs = {"holdings": f"fund_holdings amfi_code={code} month={holdings_month}",
            "constituents": f"index_constituents {bench_name!r} month={index_month}",
            "returns": f"fund_nav amfi_code={code} vs index_prices {bench_name!r} (price index, not TRI) ..{as_of}",
            "ter": f"fund_expense_ratios amfi_code={code} as_of={ter[1] if ter else None}"}

    share = None
    if bench_name is None:
        gaps.append(Gap(item="benchmark", reason=f"no benchmark index mapped for category {category or scheme['category']!r}"))
    elif not holdings or holdings_month is None:
        gaps.append(Gap(item="portfolio", reason=f"no portfolio disclosure on or before {as_of}"))
    elif (as_of - holdings_month).days > MAX_PORTFOLIO_AGE_DAYS:
        gaps.append(Gap(item="portfolio", reason=f"latest portfolio is for {holdings_month:%Y-%m}, too old for {as_of}"))
    elif not index or index_month is None or not 0 <= (holdings_month - index_month).days <= MAX_CONSTITUENT_LAG_DAYS:
        gaps.append(Gap(item="benchmark constituents", reason=f"{bench_name} weights for {holdings_month:%Y-%m} not loaded"))
    elif abs(sum(index.values()) - 100) > INDEX_WEIGHT_TOLERANCE:
        gaps.append(Gap(item="benchmark constituents", reason=f"{bench_name} weights sum to {sum(index.values()):.1f}%"))
    else:
        share = active_share(holdings, index)
        add("active_share", f"Active share vs {bench_name}, {holdings_month:%b %Y} portfolio", share, "active_share", "holdings")
        add("positions", "Stocks and other ISIN positions held", float(len(holdings)), "count", "holdings", None)
        add("benchmark_weight_held", f"Share of the portfolio in {bench_name} constituents",
            sum(w for k, w in holdings.items() if k in index) / 100, "overlap_weight", "constituents")

    te = None
    if bench_name is not None:
        if not is_growth(scheme["option"]):
            gaps.append(Gap(item="tracking error", reason=f"option {scheme['option']!r} is not Growth; IDCW NAVs drop on payouts"))
        else:
            ends = month_ends(as_of, TE_MONTHS + 1)
            diffs = [f - b for f, b in zip(monthly_returns(fund_nav, ends), monthly_returns(bench, ends))
                     if f is not None and b is not None]
            if len(diffs) < MIN_TE_MONTHS:
                gaps.append(Gap(item="tracking error", reason=f"{len(diffs)} months of fund and {bench_name} returns, "
                                                              f"needs {MIN_TE_MONTHS}"))
            else:
                te = tracking_error(diffs)
                add(f"tracking_error_{TE_MONTHS}m", f"Tracking error vs {bench_name} (price index, not TRI), annualised",
                    te, f"tracking_error_{TE_MONTHS}", "returns")
                add("tracking_error_months", "Months in tracking error", float(len(diffs)), f"tracking_error_{TE_MONTHS}", "returns", None)

    if ter is None:
        gaps.append(Gap(item="expense ratio", reason=f"no TER on or before {as_of}"))
    else:
        add("ter", f"Total expense ratio as of {ter[1]}", ter[0] / 100, "ter", "ter")
        if share:
            add("active_fee", "Expense ratio per unit of active share", active_fee(ter[0], share), "active_fee", "ter")

    if share is not None and te is not None and share < CLOSET_MAX_ACTIVE_SHARE and te < CLOSET_MAX_TRACKING_ERROR:
        flags.append(Flag(severity="watch", reason=f"active share {share:.0%} and tracking error {te:.1%} are within the "
                                                   f"closet-indexer range (active share below {CLOSET_MAX_ACTIVE_SHARE:.0%}, "
                                                   f"tracking error below {CLOSET_MAX_TRACKING_ERROR:.0%})",
                          metric_ids=["active_share", f"tracking_error_{TE_MONTHS}m"]))

    if share is None:
        confidence = Confidence(level="low", reason="active share not computable")
    else:
        caveats = [g.item for g in gaps] + (["tracking error uses a price index, not TRI"] if te is not None else [])
        confidence = Confidence(level="medium" if caveats else "high", reason="; ".join(caveats) or "all inputs current")
    used = {m.source_query_id for m in metrics}
    return Envelope(agent_id=AGENT_ID, subject=code, as_of=datetime.combine(as_of, NAV_TIME, IST), metrics=metrics,
                    findings=[], flags=flags, confidence=confidence, gaps=gaps,
                    sources=[Source(id=s, kind="dataset", ref=r, retrieved_at=retrieved_at) for s, r in refs.items() if s in used])


# --- run ------------------------------------------------------------------------------------------------

def load_inputs(conn: psycopg.Connection, code: str, as_of: date):
    """(scheme, holdings, holdings month, index weights, index month, ter, fund NAVs, benchmark, providers)."""
    row = conn.execute("SELECT amfi_code, name, category, plan, option FROM fund_schemes WHERE amfi_code = %s", (code,)).fetchone()
    if row is None:
        raise LookupError(f"unknown scheme {code!r}")
    scheme = dict(zip(("amfi_code", "name", "category", "plan", "option"), row))
    bench_name = BENCHMARK.get(sebi_category(scheme["category"]))
    prov: dict[str, set[str]] = {k: set() for k in ("holdings", "constituents", "returns", "ter")}

    holdings, h_month = {}, None
    for month, isin, w, src in conn.execute(
        """SELECT month, isin, weight_pct, source FROM fund_holdings WHERE amfi_code = %s
           AND month = (SELECT max(month) FROM fund_holdings WHERE amfi_code = %s AND month <= %s)""", (code, code, as_of)):
        holdings[isin], h_month = float(w), month
        prov["holdings"].add(src)
    index, i_month = {}, None
    if bench_name and h_month:
        for month, isin, w, src in conn.execute(
            """SELECT month, isin, weight_pct, source FROM index_constituents WHERE index_name = %s
               AND month = (SELECT max(month) FROM index_constituents WHERE index_name = %s AND month <= %s)""",
            (bench_name, bench_name, h_month)):
            index[isin], i_month = float(w), month
            prov["constituents"].add(src)
    ter_row = conn.execute("SELECT ter_pct, as_of, source FROM fund_expense_ratios WHERE amfi_code = %s AND as_of <= %s"
                           " ORDER BY as_of DESC LIMIT 1", (code, as_of)).fetchone()
    ter = (float(ter_row[0]), ter_row[1]) if ter_row else None
    if ter_row:
        prov["ter"].add(ter_row[2])

    since = as_of - timedelta(days=31 * (TE_MONTHS + 2))
    fund_nav, bench = [], []
    for d, v, src in conn.execute("SELECT nav_date, nav, source FROM fund_nav WHERE amfi_code = %s AND nav_date BETWEEN %s AND %s"
                                  " ORDER BY 1", (code, since, as_of)):
        fund_nav.append((d, float(v)))
        prov["returns"].add(src)
    if bench_name:
        for d, v, src in conn.execute("SELECT trade_date, close, source FROM index_prices WHERE index_name = %s"
                                      " AND trade_date BETWEEN %s AND %s ORDER BY 1", (bench_name, since, as_of)):
            bench.append((d, float(v)))
            prov["returns"].add(src)
    # active share is derived from both holdings and constituents
    prov["holdings"] |= prov["constituents"]
    return (scheme, holdings, h_month, index, i_month, ter, fund_nav, bench, {k: sorted(v) for k, v in prov.items()})


def run(conn: psycopg.Connection, inp: MF3Input) -> Envelope:
    """Compute and log to agent_runs/agent_outputs; failures are logged and re-raised."""
    def go():
        *inputs, providers = load_inputs(conn, inp.amfi_code, inp.as_of)
        return compute(*inputs, inp.as_of, datetime.now(IST)), providers

    return run_logged(conn, AGENT_ID, inp.amfi_code, datetime.combine(inp.as_of, NAV_TIME, IST), go)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("amfi_code")
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        print(run(conn, MF3Input(amfi_code=args.amfi_code, as_of=args.as_of)).model_dump_json(indent=2))
