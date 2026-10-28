"""M7 Valuation: compute layer only (no LLM). prices_daily + fundamentals_quarterly -> standard envelope.

P/E, P/B and EV/EBITDA now, against the company's own year-end history (10 years) and industry peers, plus the
growth rate the current market cap implies under stated discount rates (reverse DCF).
Output rule (SPEC): ranges and implied assumptions only, never a target price or "fair value".
Usage: uv run python -m services.agents.desks.markets.m7_valuation RELIANCE [--as-of 2026-10-27]
"""

import argparse
import statistics
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from db import DATABASE_URL
from services.agents.desks.funds.mf2_manager import value_on
from services.agents.desks.markets.m1_technicals import Bar, corporate_action_breaks
from services.agents.desks.markets.m2_fundamentals import (
    Statement, debt, ebitda, fy_label, load_inputs as load_financials, pick_statements, ttm_yoy, years_back,
)
from services.agents.envelope import Confidence, Envelope, Flag, Gap, Metric, Source
from services.agents.runlog import run_logged

AGENT_ID = "M7"
IST = ZoneInfo("Asia/Kolkata")
MARKET_CLOSE = dtime(15, 30)
MULTIPLES = {"pe": "P/E", "pb": "P/B", "ev_ebitda": "EV/EBITDA"}
HISTORY_YEARS = 10
MIN_HISTORY = 5
MIN_PEERS = 3
MAX_PEERS = 30
PRICE_STALE_DAYS = 7
# Reverse-DCF assumptions, shown with the result: equity cash flows ~ PAT, 10 high-growth years, then a 5%
# terminal growth (nominal INR). The range comes from these three discount rates.
# ponytail: PAT as the cash-flow proxy; switch to FCFE once capex/working-capital data is reliable per company.
DISCOUNT_RATES = (0.11, 0.12, 0.13)
DCF_YEARS = 10
TERMINAL_GROWTH = 0.05

Prices = list[tuple[date, float]]


class M7Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: str = Field(min_length=1)  # NSE symbol or ISIN
    as_of: date


# --- formulas -------------------------------------------------------------------------------------------

def multiples(bs: dict[str, float], pat: float | None, ebitda_v: float | None, price: float) -> dict[str, float]:
    """mcap = price x shares (INR x crore shares = INR crore); P/E = mcap / PAT (PAT > 0); P/B = mcap / owners'
    equity (> 0); EV/EBITDA = (mcap + debt - cash) / EBITDA (> 0). Multiples with a non-positive base are left out."""
    shares = bs.get("shares_outstanding")
    if not shares or shares <= 0:
        return {}
    mcap = price * shares
    out = {"mcap": mcap}
    if pat is not None and pat > 0:
        out["pe"] = mcap / pat
    if bs.get("equity_owners", 0) > 0:
        out["pb"] = mcap / bs["equity_owners"]
    d, cash = debt(bs), bs.get("cash_and_equivalents")
    if d is not None and cash is not None and ebitda_v is not None and ebitda_v > 0:
        out["ev"] = mcap + d - cash
        out["ev_ebitda"] = out["ev"] / ebitda_v
    return out


def band(history: list[float], current: float) -> dict:
    """min / median / max of history and the share of history points below current (0 = lowest ever, 1 = highest)."""
    return {"min": min(history), "median": statistics.median(history), "max": max(history),
            "points": len(history), "percentile": sum(h < current for h in history) / len(history)}


def dcf_value(e0: float, g: float, r: float, years: int = DCF_YEARS, tg: float = TERMINAL_GROWTH) -> float:
    """Present value of e0 growing at g for `years`, then a Gordon terminal value growing at tg, discounted at r."""
    v, e = 0.0, e0
    for t in range(1, years + 1):
        e *= 1 + g
        v += e / (1 + r) ** t
    return v + e * (1 + tg) / (r - tg) / (1 + r) ** years


def implied_growth(mcap: float, e0: float, r: float, years: int = DCF_YEARS, tg: float = TERMINAL_GROWTH,
                   lo: float = -0.5, hi: float = 1.0) -> float | None:
    """reverse_dcf: the g for which dcf_value(e0, g, r) = mcap, by bisection; None if e0 <= 0 or g is outside [lo, hi]."""
    if e0 <= 0 or r <= tg:
        return None
    f = lambda g: dcf_value(e0, g, r, years, tg) - mcap  # noqa: E731
    if f(lo) > 0 or f(hi) < 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


def current_multiples(rows: list[tuple], prices: Prices, as_of: date) -> tuple[dict, dict] | str:
    """Multiples on the latest close: TTM profit when 8 quarters exist (else the latest year's), latest year-end
    balance sheet and EBITDA. Returns (multiples, context) or a reason they can't be computed."""
    picked = pick_statements(rows, as_of)
    if picked is None:
        return "no annual financials"
    consolidated, annual, quarters, fy = picked
    prices = [p for p in prices if p[0] <= as_of]
    if not prices or (as_of - prices[-1][0]).days > PRICE_STALE_DAYS:
        return f"no close within {PRICE_STALE_DAYS} days of {as_of}"
    after_fy = [Bar(d, 0, 0, 0, c) for d, c in prices if d >= fy]
    if breaks := corporate_action_breaks(after_fy):
        return f"possible split/bonus on {breaks[-1]} after the {fy_label(fy)} share count"
    ttm = ttm_yoy(quarters, "pat_owners")
    pat, pat_basis = (ttm[0], "TTM") if ttm else (annual[fy].get("pat_owners"), fy_label(fy))
    m = multiples(annual[fy], pat, ebitda(annual[fy]), prices[-1][1])
    if not m:
        return f"no shares_outstanding for {fy_label(fy)}"
    return m, {"fy": fy, "consolidated": consolidated, "pat": pat, "pat_basis": pat_basis, "price_day": prices[-1][0],
               "annual": annual}


def history_multiples(annual: Statement, prices: Prices, as_of: date, years: int = HISTORY_YEARS) -> dict[str, list[float]]:
    """Each multiple at every year-end in the window: year-end close x year-end shares, that year's PAT/EBITDA/equity."""
    out: dict[str, list[float]] = {k: [] for k in MULTIPLES}
    start = years_back(as_of, years)
    for fy, p in sorted(annual.items()):
        if not start <= fy <= as_of or (price := value_on(prices, fy)) is None:
            continue
        for k, v in multiples(p, p.get("pat_owners"), ebitda(p), price).items():
            if k in out:
                out[k].append(v)
    return out


# --- envelope -------------------------------------------------------------------------------------------

def compute(isin: str, symbol: str | None, rows: list[tuple], prices: Prices,
            peers: dict[str, tuple[list[tuple], Prices]], as_of: date, retrieved_at: datetime) -> Envelope:
    subject = symbol or isin
    metrics: list[Metric] = []
    flags: list[Flag] = []
    gaps: list[Gap] = []

    def add(mid: str, name: str, value: float | str, formula: str, unit: str | None = "x", source: str = "valuation"):
        metrics.append(Metric(id=mid, name=name, value=value, unit=unit, formula_id=formula, source_query_id=source))

    def envelope(confidence: Confidence, day: date) -> Envelope:
        refs = {"valuation": f"prices_daily NSE + fundamentals_quarterly isin={isin} ..{as_of}",
                "peers": f"prices_daily + fundamentals_quarterly for {len(peers)} same-industry securities ..{as_of}"}
        used = {m.source_query_id for m in metrics}
        return Envelope(agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(day, MARKET_CLOSE, IST), metrics=metrics,
                        findings=[], flags=flags, confidence=confidence, gaps=gaps,
                        sources=[Source(id=s, kind="dataset", ref=r, retrieved_at=retrieved_at) for s, r in refs.items() if s in used])

    cur = current_multiples(rows, prices, as_of)
    if isinstance(cur, str):
        gaps.append(Gap(item="current valuation", reason=cur))
        if "split/bonus" in cur:
            flags.append(Flag(severity="watch", reason=cur + "; multiples withheld"))
        return envelope(Confidence(level="low", reason="current multiples not computable"), as_of)
    now, ctx = cur
    add("mcap", f"Market cap at {ctx['price_day']} close", now["mcap"], "mcap", "INR cr")
    if "ev" in now:
        add("ev", "Enterprise value (market cap + debt - cash)", now["ev"], "ev", "INR cr")

    hist = history_multiples(ctx["annual"], prices, as_of)
    peer_now = {k: [] for k in MULTIPLES}
    for p_rows, p_prices in peers.values():
        if not isinstance(pc := current_multiples(p_rows, p_prices, as_of), str):
            for k in MULTIPLES:
                if k in pc[0]:
                    peer_now[k].append(pc[0][k])

    for k, label in MULTIPLES.items():
        name = f"{label} ({ctx['pat_basis']} profit)" if k == "pe" else label
        if k not in now:
            gaps.append(Gap(item=label, reason="base (profit, equity or EBITDA) not positive or inputs missing"))
            continue
        add(k, name, now[k], k)
        h = hist[k]
        if len(h) >= MIN_HISTORY:
            b = band(h, now[k])
            add(f"{k}_hist_min", f"{label}, lowest year-end in {HISTORY_YEARS} years", b["min"], f"{k}_band_{HISTORY_YEARS}y")
            add(f"{k}_hist_median", f"{label}, median year-end in {HISTORY_YEARS} years", b["median"], f"{k}_band_{HISTORY_YEARS}y")
            add(f"{k}_hist_max", f"{label}, highest year-end in {HISTORY_YEARS} years", b["max"], f"{k}_band_{HISTORY_YEARS}y")
            add(f"{k}_hist_percentile", f"Share of year-end {label} readings below today", b["percentile"],
                f"{k}_band_{HISTORY_YEARS}y", "ratio")
            add(f"{k}_hist_points", f"Year-end {label} readings", float(b["points"]), f"{k}_band_{HISTORY_YEARS}y", None)
            if now[k] > b["max"] or now[k] < b["min"]:
                where = "above" if now[k] > b["max"] else "below"
                flags.append(Flag(severity="info", reason=f"{label} is {where} its {HISTORY_YEARS}-year year-end range",
                                  metric_ids=[k, f"{k}_hist_min", f"{k}_hist_max"]))
        else:
            gaps.append(Gap(item=f"{label} history band", reason=f"{len(h)} year-end readings, needs {MIN_HISTORY}"))
        pv = peer_now[k]
        if len(pv) >= MIN_PEERS:
            add(f"{k}_peer_median", f"{label}, industry peer median", statistics.median(pv), f"{k}_peers", "x", "peers")
            add(f"{k}_peer_percentile", f"Share of peers with a lower {label}", sum(p < now[k] for p in pv) / len(pv),
                f"{k}_peers", "ratio", "peers")
            add(f"{k}_peer_count", f"Peers with a {label}", float(len(pv)), f"{k}_peers", None, "peers")
        else:
            gaps.append(Gap(item=f"{label} vs peers", reason=f"{len(pv)} peers with a {label}, needs {MIN_PEERS} "
                                                             "(industry classification may be missing)"))

    implied = {r: implied_growth(now["mcap"], ctx["pat"], r) for r in DISCOUNT_RATES} if ctx["pat"] else {}
    if implied and all(v is not None for v in implied.values()):
        for r, g in implied.items():
            add(f"implied_growth_r{round(r * 100)}", f"Profit growth for {DCF_YEARS} years implied by today's market cap "
                f"at a {r:.0%} discount rate, {TERMINAL_GROWTH:.0%} terminal growth", g, "reverse_dcf", "ratio")
    else:
        gaps.append(Gap(item="implied growth", reason="profit not positive, or implied growth outside -50%..+100%"))

    if not ctx["consolidated"]:
        flags.append(Flag(severity="info", reason="based on standalone financials"))
    level = "high" if not gaps else "medium" if any(m.id == "pe" or m.id == "pb" for m in metrics) else "low"
    reason = "all multiples with history and peers" if not gaps else f"{len(gaps)} gap(s)"
    return envelope(Confidence(level=level, reason=reason), ctx["price_day"])


# --- run ------------------------------------------------------------------------------------------------

def _prices(conn: psycopg.Connection, isin: str, since: date, as_of: date, sources: set[str]) -> Prices:
    out = []
    for d, c, src in conn.execute(
        "SELECT p.trade_date, p.close, p.source FROM prices_daily p JOIN securities s ON s.id = p.security_id"
        " WHERE s.isin = %s AND p.exchange = 'NSE' AND p.trade_date BETWEEN %s AND %s ORDER BY 1", (isin, since, as_of)):
        out.append((d, float(c)))
        sources.add(src)
    return out


def load_inputs(conn: psycopg.Connection, subject: str, as_of: date):
    """(isin, symbol, financial rows, prices, peers {isin: (rows, prices)}, providers)."""
    isin, symbol, rows, fin_prov = load_financials(conn, subject, as_of)
    src, peer_src = set(fin_prov["financials"]), set()
    prices = _prices(conn, isin, years_back(as_of, HISTORY_YEARS + 1), as_of, src)
    peers = {}
    for (p_isin,) in conn.execute(
        """SELECT p.isin FROM securities s JOIN securities p ON p.industry = s.industry AND p.isin <> s.isin
           WHERE s.isin = %s AND s.industry IS NOT NULL AND p.nse_symbol IS NOT NULL ORDER BY p.isin LIMIT %s""",
        (isin, MAX_PEERS),
    ):
        _, _, p_rows, p_prov = load_financials(conn, p_isin, as_of)
        peer_src |= set(p_prov["financials"])
        peers[p_isin] = (p_rows, _prices(conn, p_isin, as_of - timedelta(days=400), as_of, peer_src))
    if peers:  # the peer group itself comes from the classification feed
        (cls_src,) = conn.execute("SELECT classification_source FROM securities WHERE isin = %s", (isin,)).fetchone()
        peer_src |= {cls_src} if cls_src else {"unclassified"}  # unknown provider -> never public
    return isin, symbol, rows, prices, peers, {"valuation": sorted(src), "peers": sorted(peer_src)}


def run(conn: psycopg.Connection, inp: M7Input) -> Envelope:
    """Compute and log to agent_runs/agent_outputs; failures are logged and re-raised."""
    def go():
        *inputs, providers = load_inputs(conn, inp.subject, inp.as_of)
        return compute(*inputs, inp.as_of, datetime.now(IST)), providers

    return run_logged(conn, AGENT_ID, inp.subject, datetime.combine(inp.as_of, MARKET_CLOSE, IST), go)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subject", help="NSE symbol or ISIN")
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        print(run(conn, M7Input(subject=args.subject, as_of=args.as_of)).model_dump_json(indent=2))
