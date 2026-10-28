"""MF1 Fund X-ray: compute layer only (no LLM). Portfolios + expense ratios + classification -> standard envelope.

For one or more schemes (optionally with rupee amounts): stock-level overlap between every pair, effective stock
and sector exposure of the combined holding, expense ratios, and the regular-vs-direct cost gap in rupees over
10 and 20 years under stated growth assumptions. Describes only: never says switch or exit (SPEC output rule).
Usage: uv run python -m services.agents.desks.funds.mf1_xray 122639:50000 120503:25000 [--as-of 2026-10-27]
"""

import argparse
import re
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from db import DATABASE_URL
from services.agents.desks.funds.categories import is_growth
from services.agents.desks.funds.mf3_closet_index import MAX_PORTFOLIO_AGE_DAYS
from services.agents.envelope import Confidence, Envelope, Flag, Gap, Metric, Source
from services.agents.runlog import run_logged

AGENT_ID = "MF1"
IST = ZoneInfo("Asia/Kolkata")
NAV_TIME = dtime(23, 0)
TOP_STOCKS = 10
HIGH_OVERLAP = 0.5  # pairs sharing at least half their portfolio get a descriptive flag
CONCENTRATION = 0.10  # a single stock above 10% of the combined holding gets a descriptive flag
COST_YEARS = (10, 20)
# Gross yearly growth before expenses used only to illustrate the cost gap; an assumption, not a forecast.
COST_GROWTH = (0.0, 0.08, 0.12)
ILLUSTRATIVE_AMOUNT = 100_000.0  # per Rs 1 lakh when no amount is given


class FundPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    amfi_code: str = Field(pattern=r"^\d+$")
    amount: float | None = Field(default=None, gt=0)  # rupees invested


class MF1Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    funds: list[FundPosition] = Field(min_length=1, max_length=20)
    as_of: date

    @model_validator(mode="after")
    def _distinct(self):
        codes = [f.amfi_code for f in self.funds]
        if len(codes) != len(set(codes)):
            raise ValueError("each scheme may appear once")
        return self

    @property
    def subject(self) -> str:
        return "+".join(f.amfi_code for f in self.funds)


# --- formulas -------------------------------------------------------------------------------------------

def overlap(a: dict[str, float], b: dict[str, float]) -> tuple[float, int]:
    """overlap: sum over common stocks of the smaller weight (% of net assets -> fraction), and the common count."""
    common = a.keys() & b.keys()
    return sum(min(a[k], b[k]) for k in common if a[k] > 0 and b[k] > 0) / 100, len(common)


def effective_exposure(funds: list[tuple[float, dict[str, float]]]) -> dict[str, float]:
    """effective_exposure: amount-weighted average of fund weights per ISIN, as fractions of the combined holding."""
    total = sum(amount for amount, _ in funds)
    out: dict[str, float] = {}
    for amount, holdings in funds:
        for isin, w in holdings.items():
            out[isin] = out.get(isin, 0.0) + amount / total * w / 100
    return out


def cost_gap(amount: float, ter_low: float, ter_high: float, years: int, growth: float) -> float:
    """cost_gap: amount x [(1 + g - TER_low)^n - (1 + g - TER_high)^n]; TERs as fractions, expenses taken yearly."""
    return amount * ((1 + growth - ter_low) ** years - (1 + growth - ter_high) ** years)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# --- envelope -------------------------------------------------------------------------------------------

def compute(funds: list[dict], securities: dict[str, dict], as_of: date, retrieved_at: datetime) -> Envelope:
    """funds: [{amfi_code, name, plan, option, amount, holdings {isin: %}, month, ter (pct) | None,
    counterpart: {amfi_code, plan, ter} | None | 'ambiguous'}]; securities: {isin: {symbol, sector}}."""
    metrics: list[Metric] = []
    flags: list[Flag] = []
    gaps: list[Gap] = []
    subject = "+".join(f["amfi_code"] for f in funds)

    def add(mid: str, name: str, value: float | str, formula: str, source: str, unit: str | None = "ratio"):
        metrics.append(Metric(id=mid, name=name, value=value, unit=unit, formula_id=formula, source_query_id=source))

    usable = []
    for f in funds:
        c = f["amfi_code"]
        if not f["holdings"] or f["month"] is None:
            gaps.append(Gap(item=f"{c} portfolio", reason=f"no portfolio disclosure on or before {as_of}"))
        elif (as_of - f["month"]).days > MAX_PORTFOLIO_AGE_DAYS:
            gaps.append(Gap(item=f"{c} portfolio", reason=f"latest portfolio is for {f['month']:%Y-%m}, too old"))
        else:
            usable.append(f)
            add(f"positions_{c}", f"{f['name']}: positions in {f['month']:%b %Y} portfolio", float(len(f["holdings"])),
                "count", "holdings", None)

    # pairwise overlap
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            ov, n = overlap(a["holdings"], b["holdings"])
            key = f"{a['amfi_code']}_{b['amfi_code']}"
            add(f"overlap_{key}", f"Portfolio overlap: {a['name']} / {b['name']}", ov, "overlap", "holdings")
            add(f"common_stocks_{key}", f"Stocks held by both: {a['name']} / {b['name']}", float(n), "overlap", "holdings", None)
            if ov >= HIGH_OVERLAP:
                flags.append(Flag(severity="info", reason=f"{a['name']} and {b['name']} share {ov:.0%} of their portfolios",
                                  metric_ids=[f"overlap_{key}"]))

    # effective exposure of the combined holding
    if usable and all(f["amount"] for f in usable) and len(usable) == len(funds):
        exp = effective_exposure([(f["amount"], f["holdings"]) for f in usable])
        add("total_amount", "Combined amount", sum(f["amount"] for f in usable), "sum", "holdings", "INR")
        for isin, w in sorted(exp.items(), key=lambda kv: -kv[1])[:TOP_STOCKS]:
            label = securities.get(isin, {}).get("symbol") or isin
            add(f"stock_{isin}", f"Effective weight: {label}", w, "effective_exposure", "holdings")
            if w > CONCENTRATION:
                flags.append(Flag(severity="info", reason=f"{label} is {w:.0%} of the combined holding", metric_ids=[f"stock_{isin}"]))
        sectors: dict[str, float] = {}
        for isin, w in exp.items():
            sector = securities.get(isin, {}).get("sector")
            sectors[sector or ""] = sectors.get(sector or "", 0.0) + w
        unclassified = sectors.pop("", 0.0)
        for sector, w in sorted(sectors.items(), key=lambda kv: -kv[1]):
            add(f"sector_{slug(sector)}", f"Effective sector weight: {sector}", w, "effective_exposure", "classification")
        add("unclassified_weight", "Weight in holdings without a sector (debt, cash, unclassified)", unclassified,
            "effective_exposure", "classification")
        if sectors == {}:
            gaps.append(Gap(item="sector exposure", reason="no holding has a sector classification"))
    else:
        gaps.append(Gap(item="effective exposure", reason="needs an amount for every fund and a current portfolio for each"))

    # expense ratios and the regular-vs-direct gap
    for f in funds:
        c, cp = f["amfi_code"], f["counterpart"]
        if f["ter"] is None:
            gaps.append(Gap(item=f"{c} expense ratio", reason=f"no TER on or before {as_of}"))
            continue
        add(f"ter_{c}", f"{f['name']} ({f['plan'] or 'plan unknown'}): expense ratio", f["ter"] / 100, "ter", "ter")
        if f["plan"] is None or not isinstance(cp, dict) or cp["ter"] is None:
            why = ("plan unknown" if f["plan"] is None else "more than one matching plan" if cp == "ambiguous"
                   else "no matching plan found" if cp is None else "other plan has no TER")
            gaps.append(Gap(item=f"{c} regular vs direct", reason=why))
            continue
        ter_direct, ter_regular = (f["ter"], cp["ter"]) if f["plan"] == "direct" else (cp["ter"], f["ter"])
        add(f"ter_gap_{c}", f"{f['name']}: regular minus direct expense ratio", (ter_regular - ter_direct) / 100, "ter_gap", "ter")
        amount = f["amount"] or ILLUSTRATIVE_AMOUNT
        basis = f"Rs {amount:,.0f}" if f["amount"] else "per Rs 1 lakh"
        for n in COST_YEARS:
            for g in COST_GROWTH:
                add(f"cost_gap_{c}_{n}y_g{round(g * 100)}",
                    f"{f['name']}: extra cost of the regular plan over {n} years, {basis}, assuming {g:.0%} gross growth a "
                    "year (assumption, not a forecast)",
                    cost_gap(amount, ter_direct / 100, ter_regular / 100, n, g), f"cost_gap_{n}", "ter", "INR")

    if not usable:
        confidence = Confidence(level="low", reason="no current portfolio for any fund")
    elif gaps:
        confidence = Confidence(level="medium", reason="; ".join(g.item for g in gaps))
    else:
        confidence = Confidence(level="high", reason="current portfolios and expense ratios for every fund")
    refs = {"holdings": f"fund_holdings latest month on or before {as_of} for {subject}",
            "ter": f"fund_expense_ratios for {subject} and their other plans",
            "classification": "securities sector classification of holdings"}
    used = {m.source_query_id for m in metrics}
    return Envelope(agent_id=AGENT_ID, subject=subject, as_of=datetime.combine(as_of, NAV_TIME, IST), metrics=metrics,
                    findings=[], flags=flags, confidence=confidence, gaps=gaps,
                    sources=[Source(id=s, kind="dataset", ref=r, retrieved_at=retrieved_at) for s, r in refs.items() if s in used])


# --- run ------------------------------------------------------------------------------------------------

def _ter(conn, code: str, as_of: date, prov: set[str]) -> float | None:
    row = conn.execute("SELECT ter_pct, source FROM fund_expense_ratios WHERE amfi_code = %s AND as_of <= %s"
                       " ORDER BY as_of DESC LIMIT 1", (code, as_of)).fetchone()
    if row is None:
        return None
    prov.add(row[1])
    return float(row[0])


def load_inputs(conn: psycopg.Connection, inp: MF1Input):
    """(funds, securities, providers)."""
    prov = {"holdings": set(), "ter": set(), "classification": set()}
    funds = []
    for pos in inp.funds:
        row = conn.execute("SELECT amfi_code, name, amc, category, plan, option FROM fund_schemes WHERE amfi_code = %s",
                           (pos.amfi_code,)).fetchone()
        if row is None:
            raise LookupError(f"unknown scheme {pos.amfi_code!r}")
        code, name, amc, category, plan, option = row
        holdings, month = {}, None
        for m, isin, w, src in conn.execute(
            """SELECT month, isin, weight_pct, source FROM fund_holdings WHERE amfi_code = %s
               AND month = (SELECT max(month) FROM fund_holdings WHERE amfi_code = %s AND month <= %s)""",
            (code, code, inp.as_of)):
            holdings[isin], month = float(w), m
            prov["holdings"].add(src)
        # The other plan of the same scheme: identical AMC, name, category, both Growth, plan differs. Exact match
        # on published fields only; zero or several matches stay unresolved.
        counterpart = None
        if plan and is_growth(option):
            matches = [c for c, opt in conn.execute(
                "SELECT amfi_code, option FROM fund_schemes WHERE amc = %s AND name = %s AND category = %s"
                " AND plan IS NOT NULL AND plan <> %s AND scheme_type = 'open'", (amc, name, category, plan)) if is_growth(opt)]
            if len(matches) > 1:
                counterpart = "ambiguous"
            elif matches:
                counterpart = {"amfi_code": matches[0], "plan": "regular" if plan == "direct" else "direct",
                               "ter": _ter(conn, matches[0], inp.as_of, prov["ter"])}
        funds.append({"amfi_code": code, "name": name, "plan": plan, "option": option, "amount": pos.amount,
                      "holdings": holdings, "month": month, "ter": _ter(conn, code, inp.as_of, prov["ter"]),
                      "counterpart": counterpart})
    isins = sorted({i for f in funds for i in f["holdings"]})
    securities = {}
    for isin, symbol, sector, src in conn.execute(
        "SELECT isin, nse_symbol, sector, classification_source FROM securities WHERE isin = ANY(%s)", (isins,)):
        securities[isin] = {"symbol": symbol, "sector": sector}
        if sector:
            prov["classification"].add(src or "unclassified")  # a sector without a recorded source is never public
    prov["classification"] |= prov["holdings"]  # sector weights are built from the holdings too
    return funds, securities, {k: sorted(v) for k, v in prov.items()}


def run(conn: psycopg.Connection, inp: MF1Input) -> Envelope:
    """Compute and log to agent_runs/agent_outputs; failures are logged and re-raised."""
    def go():
        *inputs, providers = load_inputs(conn, inp)
        return compute(*inputs, inp.as_of, datetime.now(IST)), providers

    return run_logged(conn, AGENT_ID, inp.subject, datetime.combine(inp.as_of, NAV_TIME, IST), go)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("funds", nargs="+", help="AMFI_CODE or AMFI_CODE:AMOUNT")
    ap.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(IST).date())
    args = ap.parse_args()
    positions = [FundPosition(amfi_code=c, amount=float(a) if a else None) for c, _, a in (f.partition(":") for f in args.funds)]
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        print(run(conn, MF1Input(funds=positions, as_of=args.as_of)).model_dump_json(indent=2))
