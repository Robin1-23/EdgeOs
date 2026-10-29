"""Every specialist the mother agent can dispatch: what subject it takes, how fresh its cache must be, what it
waits for, how to run it (logged) and how to recompute it for the verifier (no logging, no LLM calls)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg

from services.agents.desks.funds import mf1_xray, mf2_manager, mf3_closet_index
from services.agents.desks.markets import (
    m1_technicals, m2_fundamentals, m3_forensics, m4_filings, m5_news, m6_peers, m7_valuation,
)
from services.agents.envelope import Envelope

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Spec:
    agent_id: str
    kind: str  # "stock" (NSE symbol / ISIN) or "fund" (AMFI code)
    freshness_days: int  # a cached output this old (by run as_of) is still reused
    deadline_s: int  # time budget for a fresh run
    depends_on: tuple[str, ...]  # agents whose cached output this one reads
    run: Callable[[psycopg.Connection, str, date], Envelope]
    recompute: Callable[[psycopg.Connection, str, date], Envelope]


def _now() -> datetime:
    return datetime.now(IST)


def _plain(mod, input_cls, field="subject"):
    """run/recompute for agents whose load_inputs(conn, subject, as_of) feeds compute(*inputs, as_of, now)."""
    def run(conn, subject, as_of):
        return mod.run(conn, input_cls(**{field: subject, "as_of": as_of}))

    def recompute(conn, subject, as_of):
        *inputs, _providers = mod.load_inputs(conn, subject, as_of)
        return mod.compute(*inputs, as_of, _now())
    return run, recompute


def _m4():
    def run(conn, subject, as_of):
        return m4_filings.run(conn, m4_filings.M4Input(subject=subject, as_of=as_of))

    def recompute(conn, subject, as_of):
        isin, symbol, filings, _ = m4_filings.load_inputs(conn, subject, as_of)
        summaries = m4_filings.summarize(conn, symbol or isin, filings, cache_only=True)
        return m4_filings.compute(isin, symbol, filings, summaries, as_of, _now())
    return run, recompute


def _m5():
    def run(conn, subject, as_of):
        return m5_news.run(conn, m5_news.M5Input(subject=subject, as_of=as_of))

    def recompute(conn, subject, as_of):
        inp = m5_news.M5Input(subject=subject, as_of=as_of)
        sec_id, isin, symbol, display, matched, _ = m5_news.load_inputs(conn, inp)
        labels, _err = m5_news.label(conn, sec_id, display, matched, cache_only=True)
        return m5_news.compute(isin, symbol, matched, labels, None, as_of, _now())
    return run, recompute


def _mf1():
    def inp(subject, as_of):
        return mf1_xray.MF1Input(funds=[{"amfi_code": c} for c in subject.split("+")], as_of=as_of)

    def run(conn, subject, as_of):
        return mf1_xray.run(conn, inp(subject, as_of))

    def recompute(conn, subject, as_of):
        *inputs, _ = mf1_xray.load_inputs(conn, inp(subject, as_of))
        return mf1_xray.compute(*inputs, as_of, _now())
    return run, recompute


AGENTS: dict[str, Spec] = {
    s.agent_id: s for s in [
        Spec("M1", "stock", 1, 20, (), *_plain(m1_technicals, m1_technicals.M1Input)),
        Spec("M2", "stock", 7, 20, (), *_plain(m2_fundamentals, m2_fundamentals.M2Input)),
        Spec("M3", "stock", 7, 20, (), *_plain(m3_forensics, m3_forensics.M3Input)),
        Spec("M4", "stock", 1, 90, (), *_m4()),
        Spec("M5", "stock", 1, 60, (), *_m5()),
        Spec("M7", "stock", 1, 20, ("M2",), *_plain(m7_valuation, m7_valuation.M7Input)),
        Spec("M6", "stock", 7, 20, ("M2", "M7"), *_plain(m6_peers, m6_peers.M6Input)),
        Spec("MF1", "fund", 31, 20, (), *_mf1()),
        Spec("MF2", "fund", 7, 30, (), *_plain(mf2_manager, mf2_manager.MF2Input, "amfi_code")),
        Spec("MF3", "fund", 31, 20, (), *_plain(mf3_closet_index, mf3_closet_index.MF3Input, "amfi_code")),
    ]
}
PAGE_AGENTS = {"stock": ["M1", "M2", "M3", "M4", "M5", "M7", "M6"], "fund": ["MF1", "MF2", "MF3"]}
