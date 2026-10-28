"""MF2 Manager and consistency: compute layer only (no LLM). fund_nav + peers + index_prices -> standard envelope.

Covers returns and consistency vs category; manager tenure / change analysis needs AMC factsheet data (gap).
Usage: uv run python -m services.agents.desks.funds.mf2_manager 122639 [--as-of 2026-10-27]
"""

import argparse
import bisect
import calendar
import statistics
from collections.abc import Sequence
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.desks.funds.categories import BENCHMARK, is_growth, sebi_category
from services.agents.runlog import run_logged
from services.agents.envelope import Confidence, Envelope, Flag, Gap, Metric, Source

AGENT_ID = "MF2"
IST = ZoneInfo("Asia/Kolkata")
NAV_TIME = dtime(23, 0)  # AMFI publishes the day's NAVs by ~23:00 IST
HORIZONS = (1, 3, 5)  # years
MAX_STALE_DAYS = 7  # a NAV older than this before a date doesn't count as "the NAV on that date"
MIN_PEERS = 5
ROLLING_WINDOWS = 60  # month-end windows examined for consistency (5 years of observations)
CAPTURE_MONTHS = (36, 60)
MIN_DOWN_MONTHS = 6
HISTORY_YEARS = 11  # 5y horizon + 5y of rolling windows + slack

Series = list[tuple[date, float]]  # sorted by date


class MF2Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    amfi_code: str = Field(pattern=r"^\d+$")
    as_of: date


# --- formulas -------------------------------------------------------------------------------------------

def value_on(series: Series, d: date, max_stale: int = MAX_STALE_DAYS) -> float | None:
    """Last value on or before d, if it is at most max_stale days old (covers weekends and holidays)."""
    i = bisect.bisect_right(series, (d, float("inf"))) - 1
    if i < 0 or (d - series[i][0]).days > max_stale:
        return None
    return series[i][1]


def years_before(d: date, n: int) -> date:
    return d.replace(year=d.year - n) if not (d.month == 2 and d.day == 29) else date(d.year - n, 2, 28)


def cagr(start: float, end: float, years: float) -> float:
    """cagr_n: (end / start) ** (1 / years) - 1; for 1 year this is the simple return."""
    return (end / start) ** (1 / years) - 1


def point_return(series: Series, end: date, years: int) -> float | None:
    v0, v1 = value_on(series, years_before(end, years)), value_on(series, end)
    return cagr(v0, v1, years) if v0 and v1 else None


def category_mean(peers: dict[str, Series], end: date, years: int, min_peers: int = MIN_PEERS) -> tuple[float, int] | None:
    """category_mean_n: mean of peer returns over the same window; None below min_peers."""
    rs = [r for s in peers.values() if (r := point_return(s, end, years)) is not None]
    return (statistics.fmean(rs), len(rs)) if len(rs) >= min_peers else None


def month_ends(last: date, count: int) -> list[date]:
    """The count most recent calendar month-ends on or before last, oldest first."""
    y, m = last.year, last.month
    if last.day != calendar.monthrange(y, m)[1]:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    out = []
    for _ in range(count):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def rolling_vs_category(fund: Series, peers: dict[str, Series], as_of: date, years: int,
                        windows: int = ROLLING_WINDOWS) -> dict | None:
    """rolling_beat_n: over month-end windows, share where the fund's return beat the category mean,
    plus median and worst fund return. None if no window has both."""
    pairs = []
    for end in month_ends(as_of, windows):
        f, c = point_return(fund, end, years), category_mean(peers, end, years)
        if f is not None and c is not None:
            pairs.append((f, c[0]))
    if not pairs:
        return None
    fund_rs = [f for f, _ in pairs]
    return {"windows": len(pairs), "beat_share": sum(f > c for f, c in pairs) / len(pairs),
            "median": statistics.median(fund_rs), "worst": min(fund_rs)}


def monthly_returns(series: Series, ends: Sequence[date]) -> list[float | None]:
    vals = [value_on(series, d) for d in ends]
    return [b / a - 1 if a and b else None for a, b in zip(vals, vals[1:])]


def downside_capture(fund_r: Sequence[float], bench_r: Sequence[float], min_down: int = MIN_DOWN_MONTHS) -> tuple[float, int] | None:
    """downside_capture_n: over months where the benchmark fell, geometric mean fund return / geometric mean
    benchmark return. 1.0 = fell as much as the benchmark; below 1 = fell less."""
    down = [(f, b) for f, b in zip(fund_r, bench_r) if b < 0]
    if len(down) < min_down:
        return None
    k = len(down)
    gf = statistics.geometric_mean([1 + f for f, _ in down]) - 1
    gb = statistics.geometric_mean([1 + b for _, b in down]) - 1
    return gf / gb, k


# --- envelope -------------------------------------------------------------------------------------------

def compute(scheme: dict, fund: Series, peers: dict[str, Series], bench: Series, as_of: date, retrieved_at: datetime) -> Envelope:
    code = scheme["amfi_code"]
    fund = [p for p in fund if p[0] <= as_of]
    metrics: list[Metric] = []
    flags: list[Flag] = []
    gaps: list[Gap] = [Gap(item="fund manager history", reason="manager tenure and changes need AMC factsheet data, not loaded")]
    category = sebi_category(scheme["category"])
    bench_name = BENCHMARK.get(category) if category else None

    def add(mid: str, name: str, value: float | str, formula: str, unit: str | None = None, source: str = "nav"):
        metrics.append(Metric(id=mid, name=name, value=value, unit=unit, formula_id=formula, source_query_id=source))

    def envelope(confidence: Confidence) -> Envelope:
        used = {m.source_query_id for m in metrics}
        refs = {
            "nav": f"fund_nav amfi_code={code} ..{as_of}",
            "peers": f"fund_nav peers: SEBI category {category!r}, plan {scheme['plan']}, growth options ..{as_of}",
            "benchmark": f"index_prices {bench_name!r} (price index, not TRI) ..{as_of}",
        }
        last_day = fund[-1][0] if fund else as_of
        return Envelope(
            agent_id=AGENT_ID, subject=code, as_of=datetime.combine(last_day, NAV_TIME, IST), metrics=metrics, findings=[],
            flags=flags, confidence=confidence, gaps=gaps,
            sources=[Source(id=s, kind="dataset", ref=r, retrieved_at=retrieved_at) for s, r in refs.items() if s in used],
        )

    if not is_growth(scheme["option"]):
        gaps.append(Gap(item="returns", reason=f"option {scheme['option']!r} is not Growth; IDCW NAVs drop on payouts"))
        return envelope(Confidence(level="low", reason="not a Growth option"))
    if not fund or (as_of - fund[-1][0]).days > MAX_STALE_DAYS:
        gaps.append(Gap(item="NAV", reason=f"no NAV within {MAX_STALE_DAYS} days of {as_of}"))
        return envelope(Confidence(level="low", reason="no current NAV"))

    add("nav", "NAV", fund[-1][1], "nav", "INR")
    if category is None:
        gaps.append(Gap(item="category comparison", reason=f"category {scheme['category']!r} has no SEBI peer group mapping"))

    for n in HORIZONS:
        if (r := point_return(fund, as_of, n)) is None:
            gaps.append(Gap(item=f"{n}-year return", reason=f"no NAV within {MAX_STALE_DAYS} days of {years_before(as_of, n)}"))
            continue
        add(f"return_{n}y", f"{n}-year return (annualised)" if n > 1 else "1-year return", r, f"cagr_{n}", "ratio")
        if category is None:
            continue
        if (c := category_mean(peers, as_of, n)) is None:
            gaps.append(Gap(item=f"{n}-year category average", reason=f"fewer than {MIN_PEERS} peers with {n} years of NAVs"))
            continue
        add(f"category_return_{n}y", f"{category} category average, {n}-year", c[0], f"category_mean_{n}", "ratio", "peers")
        add(f"category_peers_{n}y", f"Peers in {n}-year category average", float(c[1]), f"category_mean_{n}", None, "peers")
        add(f"vs_category_{n}y", f"{n}-year return minus category average", r - c[0], f"category_mean_{n}", "ratio", "peers")

        if (roll := rolling_vs_category(fund, peers, as_of, n)) is None:
            gaps.append(Gap(item=f"rolling {n}-year consistency", reason="no month-end window with fund and category returns"))
            continue
        add(f"rolling_{n}y_beat_share", f"Share of rolling {n}-year windows above category average", roll["beat_share"],
            f"rolling_beat_{n}", "ratio", "peers")
        add(f"rolling_{n}y_windows", f"Rolling {n}-year windows examined", float(roll["windows"]), f"rolling_beat_{n}", None, "peers")
        add(f"rolling_{n}y_median", f"Median rolling {n}-year return", roll["median"], f"rolling_beat_{n}", "ratio")
        add(f"rolling_{n}y_worst", f"Worst rolling {n}-year return", roll["worst"], f"rolling_beat_{n}", "ratio")
        if n == 3 and roll["beat_share"] < 0.3:
            flags.append(Flag(severity="watch", reason=f"above the category average in {roll['beat_share']:.0%} of "
                                                       f"{roll['windows']} rolling 3-year windows",
                              metric_ids=["rolling_3y_beat_share", "rolling_3y_windows"]))

    if bench_name is None:
        gaps.append(Gap(item="downside capture", reason=f"no benchmark index mapped for category {category!r}"))
    elif not bench:
        gaps.append(Gap(item="downside capture", reason=f"no {bench_name} closes loaded"))
    else:
        for months in CAPTURE_MONTHS:
            ends = month_ends(as_of, months + 1)
            pairs = [(f, b) for f, b in zip(monthly_returns(fund, ends), monthly_returns(bench, ends)) if f is not None and b is not None]
            if len(pairs) < months:
                gaps.append(Gap(item=f"downside capture {months}m", reason=f"{len(pairs)} of {months} months with fund and {bench_name} data"))
                continue
            if (dc := downside_capture([f for f, _ in pairs], [b for _, b in pairs])) is None:
                gaps.append(Gap(item=f"downside capture {months}m", reason=f"fewer than {MIN_DOWN_MONTHS} down months for {bench_name}"))
                continue
            add(f"downside_capture_{months}m", f"Downside capture vs {bench_name} (price index, not TRI), {months} months",
                dc[0], f"downside_capture_{months}", "ratio", "benchmark")
            add(f"down_months_{months}m", f"{bench_name} down months in {months}", float(dc[1]), f"downside_capture_{months}",
                None, "benchmark")
            if months == 36 and dc[0] > 1:
                flags.append(Flag(severity="info", reason=f"fell more than {bench_name} on average in its down months (36m)",
                                  metric_ids=["downside_capture_36m"]))

    if not any(m.id.startswith("return_") for m in metrics):
        confidence = Confidence(level="low", reason="no fund return could be computed")
    else:
        caveats = ["no manager data"] + [g.item for g in gaps[1:]]
        if any(m.source_query_id == "benchmark" for m in metrics):
            caveats.append("benchmark is a price index, not TRI")
        confidence = Confidence(level="medium", reason="; ".join(caveats))
    return envelope(confidence)


# --- run ------------------------------------------------------------------------------------------------

def _series(rows) -> Series:
    return [(d, float(v)) for d, v in rows]


def load_inputs(conn: psycopg.Connection, code: str, as_of: date) -> tuple[dict, Series, dict[str, Series], Series]:
    row = conn.execute("SELECT amfi_code, name, category, plan, option FROM fund_schemes WHERE amfi_code = %s", (code,)).fetchone()
    if row is None:
        raise LookupError(f"unknown scheme {code!r}")
    scheme = dict(zip(("amfi_code", "name", "category", "plan", "option"), row))
    since = as_of - timedelta(days=366 * HISTORY_YEARS)
    fund = _series(conn.execute("SELECT nav_date, nav FROM fund_nav WHERE amfi_code = %s AND nav_date BETWEEN %s AND %s ORDER BY 1",
                                (code, since, as_of)))
    category = sebi_category(scheme["category"])
    peers: dict[str, Series] = {}
    if category and scheme["plan"]:
        candidates = [c for c, cat, opt in conn.execute(
            "SELECT amfi_code, category, option FROM fund_schemes WHERE scheme_type = 'open' AND plan = %s", (scheme["plan"],)
        ) if sebi_category(cat) == category and is_growth(opt)]
        for c, d, v in conn.execute(
            "SELECT amfi_code, nav_date, nav FROM fund_nav WHERE amfi_code = ANY(%s) AND nav_date BETWEEN %s AND %s ORDER BY 1, 2",
            (candidates, since, as_of),
        ):
            peers.setdefault(c, []).append((d, float(v)))
    bench: Series = []
    if (name := BENCHMARK.get(category)) is not None:
        bench = _series(conn.execute(
            "SELECT trade_date, close FROM index_prices WHERE index_name = %s AND trade_date BETWEEN %s AND %s ORDER BY 1",
            (name, since, as_of)))
    return scheme, fund, peers, bench


def run(conn: psycopg.Connection, inp: MF2Input) -> Envelope:
    """Compute and log to agent_runs/agent_outputs; failures are logged and re-raised."""
    return run_logged(conn, AGENT_ID, inp.amfi_code, datetime.combine(inp.as_of, NAV_TIME, IST),
                      lambda: compute(*load_inputs(conn, inp.amfi_code, inp.as_of), inp.as_of, datetime.now(IST)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("amfi_code")
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        print(run(conn, MF2Input(amfi_code=args.amfi_code, as_of=args.as_of)).model_dump_json(indent=2))
