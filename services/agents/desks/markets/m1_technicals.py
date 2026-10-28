"""M1 Price and technicals: compute layer only (no LLM). prices_daily + index_prices -> standard envelope.

Usage: uv run python -m services.agents.desks.markets.m1_technicals RELIANCE [--as-of 2026-10-27]
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from fractions import Fraction
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.runlog import run_logged
from services.agents.envelope import Confidence, Envelope, Flag, Gap, Metric, Source

AGENT_ID = "M1"
IST = ZoneInfo("Asia/Kolkata")
MARKET_CLOSE = dtime(15, 30)
SESSIONS_52W = 252
HISTORY_SESSIONS = 600  # enough for 252-session windows after a recent corporate-action break
STALE_DAYS = 5  # calendar days from as_of back to the latest bar (a weekend plus a holiday)
# One-day close ratios a split/bonus leaves in unadjusted prices: split 1:2 -> 1/2, bonus 1:1 -> 1/2,
# bonus 1:2 -> 2/3, bonus 1:3 -> 3/4, FV 5->2 -> 2/5. Upward only for consolidations (reverse splits), which
# are whole multiples; a +33% rally on an unbanded stock is not a 4/3 "corporate action".
CA_RATIOS = [float(Fraction(r)) for r in ["1/10", "1/5", "1/4", "1/3", "2/5", "1/2", "2/3", "3/4", "2", "3", "4", "5", "10"]]
CA_TOLERANCE = 0.03
CA_MIN_PRICE = 2.0  # below this, tick size (0.05) alone makes 1/2 and 2x moves: 0.05 <-> 0.10


class M1Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: str = Field(min_length=1)  # NSE symbol or ISIN
    as_of: date


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float


# --- formulas (each formula_id below names one of these) ---------------------------------------------

def sma(xs: Sequence[float], n: int) -> float | None:
    """sma_n: mean of the last n values."""
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema_series(xs: Sequence[float], n: int) -> list[float]:
    """EMA with alpha 2/(n+1), seeded with the SMA of the first n values; item i aligns with xs[n-1+i]."""
    if len(xs) < n:
        return []
    alpha = 2 / (n + 1)
    out = [sum(xs[:n]) / n]
    for x in xs[n:]:
        out.append(alpha * x + (1 - alpha) * out[-1])
    return out


def rsi_wilder(closes: Sequence[float], n: int = 14) -> float | None:
    """rsi_wilder_14: Wilder smoothing, seeded with the simple average of the first n gains and losses."""
    if len(closes) < n + 1:
        return None
    diffs = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(d, 0) for d in diffs[:n]) / n
    loss = sum(max(-d, 0) for d in diffs[:n]) / n
    for d in diffs[n:]:
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
    if loss == 0:
        return 50.0 if gain == 0 else 100.0
    return 100 - 100 / (1 + gain / loss)


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float] | None:
    """macd_12_26_9: (EMA fast - EMA slow, EMA signal of that line, line - signal)."""
    slow_ema = ema_series(closes, slow)
    fast_ema = ema_series(closes, fast)[slow - fast:]  # align with slow_ema's first item
    line = [f - s for f, s in zip(fast_ema, slow_ema)]
    sig = ema_series(line, signal)
    if not sig:
        return None
    return line[-1], sig[-1], line[-1] - sig[-1]


def range_52w(bars: Sequence[Bar], n: int = SESSIONS_52W) -> tuple[float, float, float] | None:
    """range_252: (highest high, lowest low, (close - low) / (high - low)) over the last n sessions."""
    if len(bars) < n:
        return None
    hi, lo = max(b.high for b in bars[-n:]), min(b.low for b in bars[-n:])
    return (hi, lo, (bars[-1].close - lo) / (hi - lo)) if hi > lo else None


def rel_return(bars: Sequence[Bar], index: dict[date, float], n: int) -> float | None:
    """rel_return_n: (1 + stock return) / (1 + index return) - 1 over the stock's last n sessions."""
    if len(bars) < n + 1:
        return None
    b0, b1 = bars[-n - 1], bars[-1]
    if b0.day not in index or b1.day not in index:
        return None
    return (b1.close / b0.close) / (index[b1.day] / index[b0.day]) - 1


def trend_state(close: float, sma50: float, sma200: float) -> str:
    """trend_sma_50_200."""
    if close > sma50 > sma200:
        return "up"
    if close < sma50 < sma200:
        return "down"
    return "mixed"


def corporate_action_breaks(bars: Sequence[Bar]) -> list[date]:
    """Days whose close / previous close sits within CA_TOLERANCE of a split/bonus ratio (likely unadjusted)."""
    return [
        cur.day for prev, cur in zip(bars, bars[1:])
        if min(prev.close, cur.close) >= CA_MIN_PRICE
        and any(abs((cur.close / prev.close) / r - 1) <= CA_TOLERANCE for r in CA_RATIOS)
    ]


def sr_zones(bars: Sequence[Bar], n: int = SESSIONS_52W, k: int = 5, tol: float = 0.015,
             min_touches: int = 2) -> tuple[list[list], list[list]] | None:
    """sr_pivots_252: swing highs/lows (k sessions either side) over n sessions, grouped within tol of the
    zone's low; zones with >= min_touches. Returns (nearest 2 supports below close, nearest 2 resistances above),
    each zone [low, high, touches].
    ponytail: fixed % grouping; switch to ATR-scaled tolerance if zones look too wide/narrow across price levels."""
    if len(bars) < n:
        return None
    w = bars[-n:]
    pivots = []
    for i in range(k, len(w) - k):
        win = w[i - k:i + k + 1]
        if w[i].high == max(b.high for b in win):
            pivots.append(w[i].high)
        if w[i].low == min(b.low for b in win):
            pivots.append(w[i].low)
    zones: list[list] = []
    for p in sorted(pivots):
        if zones and p <= zones[-1][0] * (1 + tol):
            zones[-1][1] = p
            zones[-1][2] += 1
        else:
            zones.append([p, p, 1])
    zones = [z for z in zones if z[2] >= min_touches]
    close = w[-1].close
    supports = sorted((z for z in zones if z[1] < close), key=lambda z: close - z[1])[:2]
    resistances = sorted((z for z in zones if z[0] > close), key=lambda z: z[0] - close)[:2]
    return supports, resistances


# --- envelope -------------------------------------------------------------------------------------------

def compute(isin: str, symbol: str | None, bars: list[Bar], nifty: dict[date, float], as_of: date,
            retrieved_at: datetime) -> Envelope:
    bars = [b for b in bars if b.day <= as_of]
    metrics: list[Metric] = []
    flags: list[Flag] = []
    gaps: list[Gap] = []
    subject = symbol or isin

    def add(mid: str, name: str, value: float | str, formula: str, unit: str | None = None, source: str = "prices"):
        metrics.append(Metric(id=mid, name=name, value=value, unit=unit, formula_id=formula, source_query_id=source))

    def need(n: int, what: str, have: int) -> None:
        gaps.append(Gap(item=what, reason=f"needs {n} sessions, {have} usable"))

    if not bars:
        return Envelope(
            agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(as_of, MARKET_CLOSE, IST), metrics=[], findings=[],
            flags=[], sources=[], confidence=Confidence(level="low", reason="no NSE prices on or before as_of"),
            gaps=[Gap(item="prices", reason=f"no prices_daily rows for {isin} on or before {as_of}")],
        )

    breaks = corporate_action_breaks(bars)
    if breaks:
        bars = [b for b in bars if b.day >= breaks[-1]]  # only sessions after the last likely split/bonus
        flags.append(Flag(severity="watch", reason=f"possible unadjusted split/bonus on {breaks[-1]}; "
                                                   "indicators use only sessions from that day on"))
        gaps.append(Gap(item="corporate actions", reason=f"price history before {breaks[-1]} is not adjusted; excluded"))

    closes = [b.close for b in bars]
    last = bars[-1]
    add("close", "Close", last.close, "close", "INR")

    for n in (20, 50, 200):
        if (v := sma(closes, n)) is not None:
            add(f"sma_{n}", f"{n}-day moving average", v, f"sma_{n}", "INR")
        else:
            need(n, f"{n}-day moving average", len(closes))

    if (rsi := rsi_wilder(closes)) is not None:
        add("rsi_14", "RSI (14)", rsi, "rsi_wilder_14")
        if rsi >= 70 or rsi <= 30:
            flags.append(Flag(severity="info", reason=f"RSI(14) at {rsi:.1f}, {'above 70' if rsi >= 70 else 'below 30'}",
                              metric_ids=["rsi_14"]))
    else:
        need(15, "RSI (14)", len(closes))

    if (m := macd(closes)) is not None:
        for mid, name, v in zip(("macd", "macd_signal", "macd_hist"), ("MACD line", "MACD signal", "MACD histogram"), m):
            add(mid, name, v, "macd_12_26_9", "INR")
    else:
        need(34, "MACD (12, 26, 9)", len(closes))

    if (r := range_52w(bars)) is not None:
        hi, lo, pos = r
        add("high_52w", "52-week high", hi, "range_252", "INR")
        add("low_52w", "52-week low", lo, "range_252", "INR")
        add("pos_52w", "Position in 52-week range", pos, "range_252")
        if last.close >= 0.98 * hi:
            flags.append(Flag(severity="info", reason="close within 2% of the 52-week high", metric_ids=["close", "high_52w"]))
        elif last.close <= 1.02 * lo:
            flags.append(Flag(severity="info", reason="close within 2% of the 52-week low", metric_ids=["close", "low_52w"]))
    else:
        need(SESSIONS_52W, "52-week range", len(bars))

    for n, label in ((63, "3m"), (126, "6m"), (252, "12m")):
        if (v := rel_return(bars, nifty, n)) is not None:
            add(f"rs_nifty_{label}", f"Return relative to Nifty 50, {label}", v, f"rel_return_{n}", "ratio", "prices_vs_nifty")
        elif len(bars) < n + 1:
            need(n + 1, f"relative strength vs Nifty 50 ({label})", len(bars))
        else:
            gaps.append(Gap(item=f"relative strength vs Nifty 50 ({label})", reason="Nifty 50 close missing for a needed date"))

    s50, s200 = sma(closes, 50), sma(closes, 200)
    if s50 is not None and s200 is not None:
        add("trend", "Trend state", trend_state(last.close, s50, s200), "trend_sma_50_200")
        # 50/200 cross within the last 5 sessions
        if len(closes) >= 205:
            then = sma(closes[:-5], 50) - sma(closes[:-5], 200)
            if (then <= 0) != (s50 - s200 <= 0):
                kind = "above" if s50 > s200 else "below"
                flags.append(Flag(severity="info", reason=f"50-day average crossed {kind} the 200-day in the last 5 sessions",
                                  metric_ids=["sma_50", "sma_200"]))

    if (z := sr_zones(bars)) is not None:
        for kind, zones in zip(("support", "resistance"), z):
            for i, (lo_, hi_, touches) in enumerate(zones, 1):
                add(f"{kind}_{i}_low", f"{kind.title()} zone {i} low ({touches} touches)", lo_, "sr_pivots_252", "INR")
                add(f"{kind}_{i}_high", f"{kind.title()} zone {i} high ({touches} touches)", hi_, "sr_pivots_252", "INR")
    else:
        need(SESSIONS_52W, "support/resistance zones", len(bars))

    stale = (as_of - last.day).days > STALE_DAYS
    if stale:
        flags.append(Flag(severity="watch", reason=f"latest price is from {last.day}, more than {STALE_DAYS} days before {as_of}"))
    if stale or len(bars) < SESSIONS_52W:
        confidence = Confidence(level="low", reason="stale prices" if stale else f"only {len(bars)} usable sessions")
    elif gaps:
        confidence = Confidence(level="medium", reason=f"{len(gaps)} gap(s) in inputs")
    else:
        confidence = Confidence(level="high", reason=f"{len(bars)} sessions of current NSE prices")

    span = f"{bars[0].day}..{last.day}"
    sources = [Source(id="prices", kind="dataset", ref=f"prices_daily NSE isin={isin} {span}", retrieved_at=retrieved_at)]
    if any(m.source_query_id == "prices_vs_nifty" for m in metrics):
        sources.append(Source(id="prices_vs_nifty", kind="dataset",
                              ref=f"prices_daily NSE isin={isin} + index_prices 'Nifty 50' {span}", retrieved_at=retrieved_at))
    return Envelope(
        agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(last.day, MARKET_CLOSE, IST), metrics=metrics,
        findings=[], flags=flags, sources=sources, confidence=confidence, gaps=gaps,
    )


# --- run ------------------------------------------------------------------------------------------------

def load_inputs(conn: psycopg.Connection, subject: str, as_of: date) -> tuple[str, str | None, list[Bar], dict[date, float]]:
    row = conn.execute("SELECT id, isin, nse_symbol FROM securities WHERE nse_symbol = %s OR isin = %s", (subject, subject)).fetchone()
    if row is None:
        raise LookupError(f"unknown security {subject!r}")
    sid, isin, symbol = row
    rows = conn.execute(
        """SELECT trade_date, open, high, low, close FROM prices_daily
           WHERE security_id = %s AND exchange = 'NSE' AND trade_date <= %s ORDER BY trade_date DESC LIMIT %s""",
        (sid, as_of, HISTORY_SESSIONS),
    ).fetchall()
    bars = [Bar(d, float(o), float(h), float(lo), float(c)) for d, o, h, lo, c in reversed(rows)]
    nifty = {}
    if bars:
        nifty = {d: float(c) for d, c in conn.execute(
            "SELECT trade_date, close FROM index_prices WHERE index_name = 'Nifty 50' AND trade_date BETWEEN %s AND %s",
            (bars[0].day, as_of),
        )}
    return isin, symbol, bars, nifty


def run(conn: psycopg.Connection, inp: M1Input) -> Envelope:
    """Compute and log to agent_runs/agent_outputs; failures are logged and re-raised."""
    return run_logged(conn, AGENT_ID, inp.subject, datetime.combine(inp.as_of, MARKET_CLOSE, IST),
                      lambda: compute(*load_inputs(conn, inp.subject, inp.as_of), inp.as_of, datetime.now(IST)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subject", help="NSE symbol or ISIN")
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        print(run(conn, M1Input(subject=args.subject, as_of=args.as_of)).model_dump_json(indent=2))
