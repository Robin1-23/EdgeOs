import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from services.agents.desks.markets import m1_technicals as m1
from services.agents.desks.markets.m1_technicals import Bar

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=m1.IST)


def bars_from(closes, start=date(2025, 1, 1)):
    """One bar per calendar day (weekday spacing doesn't matter to the formulas); high/low +-1% of close."""
    return [Bar(start + timedelta(days=i), c, c * 1.01, c * 0.99, c) for i, c in enumerate(closes)]


# --- formulas, hand-worked ------------------------------------------------------------------------------

def test_sma():
    assert m1.sma([10, 11, 12, 13], 3) == 12  # (11 + 12 + 13) / 3
    assert m1.sma([10, 11], 3) is None


def test_ema_series():
    # n=3 -> alpha 0.5; seed SMA(10, 11, 12) = 11; next 0.5*13 + 0.5*11 = 12
    assert m1.ema_series([10, 11, 12, 13], 3) == [11, 12]


def test_rsi_wilder():
    # closes 10, 11, 12, 11, 13 -> diffs +1 +1 -1 +2; n=3
    # seed: gain (1+1+0)/3 = 2/3, loss (0+0+1)/3 = 1/3
    # next (+2): gain (2/3*2 + 2)/3 = 10/9, loss (1/3*2 + 0)/3 = 2/9 -> RS 5 -> RSI 100 - 100/6 = 83.333
    assert m1.rsi_wilder([10, 11, 12, 11, 13], 3) == pytest.approx(83.3333, abs=1e-4)
    assert m1.rsi_wilder([1, 2, 3, 4], 3) == 100.0  # no losses
    assert m1.rsi_wilder([5, 5, 5, 5], 3) == 50.0  # flat
    assert m1.rsi_wilder([1, 2, 3], 3) is None


def test_macd_small_periods():
    # closes 10 12 11 14 13 16, fast=2 (alpha 2/3), slow=3 (alpha 1/2), signal=2
    # EMA2: seed 11 @12, then 11, 13, 13, 15        EMA3: seed 11 @11, then 12.5, 12.75, 14.375
    # line (from 3rd close): 0, 0.5, 0.25, 0.625
    # signal EMA2 of line: seed 0.25, then 0.25, 2/3*0.625 + 1/3*0.25 = 0.5 -> hist 0.125
    line, sig, hist = m1.macd([10, 12, 11, 14, 13, 16], fast=2, slow=3, signal=2)
    assert (line, sig, hist) == pytest.approx((0.625, 0.5, 0.125))
    assert m1.macd([10, 12, 11], fast=2, slow=3, signal=2) is None


def test_range_52w():
    bars = [Bar(date(2026, 1, i + 1), 0, h, lo, c) for i, (h, lo, c) in enumerate([(10, 9, 9.5), (12, 10, 11), (11, 10, 10.5)])]
    assert m1.range_52w(bars, n=3) == (12, 9, 0.5)  # (10.5 - 9) / (12 - 9)
    assert m1.range_52w(bars, n=4) is None


def test_rel_return():
    bars = bars_from([100, 105, 110])
    index = {bars[0].day: 200, bars[2].day: 210}
    # stock +10%, index +5% -> 1.10 / 1.05 - 1 = 0.047619
    assert m1.rel_return(bars, index, 2) == pytest.approx(0.047619, abs=1e-6)
    assert m1.rel_return(bars, {bars[0].day: 200}, 2) is None  # index close missing
    assert m1.rel_return(bars, index, 3) is None


def test_trend_state():
    assert m1.trend_state(110, 105, 100) == "up"
    assert m1.trend_state(90, 95, 100) == "down"
    assert m1.trend_state(102, 105, 100) == "mixed"


def test_corporate_action_breaks():
    d = [date(2024, 10, 25), date(2024, 10, 28)]
    reliance_bonus = [Bar(d[0], 0, 0, 0, 2655.70), Bar(d[1], 0, 0, 0, 1334.35)]  # real 1:1 bonus ex-date, ratio 0.5024
    assert m1.corporate_action_breaks(reliance_bonus) == [d[1]]
    assert m1.corporate_action_breaks([Bar(d[0], 0, 0, 0, 100), Bar(d[1], 0, 0, 0, 85)]) == []  # 15% fall
    assert m1.corporate_action_breaks([Bar(d[0], 0, 0, 0, 100), Bar(d[1], 0, 0, 0, 80)]) == []  # 20% lower circuit
    # real false positives found in 2016-18 data, now excluded:
    assert m1.corporate_action_breaks([Bar(d[0], 0, 0, 0, 0.10), Bar(d[1], 0, 0, 0, 0.05)]) == []  # one-tick penny move
    assert m1.corporate_action_breaks([Bar(d[0], 0, 0, 0, 143.15), Bar(d[1], 0, 0, 0, 188.15)]) == []  # BANKBARODA +31%
    assert m1.corporate_action_breaks([Bar(d[0], 0, 0, 0, 20), Bar(d[1], 0, 0, 0, 100)]) == [d[1]]  # 5:1 consolidation


def test_sr_zones():
    hlc = [(10.0, 9.0, 9.5), (12.0, 11.0, 11.5), (11.0, 10.0, 10.5), (11.5, 10.1, 11.0), (12.1, 11.0, 11.8),
           (11.0, 10.05, 10.5), (11.2, 10.6, 11.0), (11.1, 10.9, 11.0), (11.3, 10.8, 11.05)]
    bars = [Bar(date(2026, 1, i + 1), 0, h, lo, c) for i, (h, lo, c) in enumerate(hlc)]
    # k=1 pivots: highs 12.0 (#1), 12.1 (#4), 11.2 (#6); lows 10.0 (#2), 10.05 (#5)
    # grouped within 1.5%: [10.0, 10.05] x2, [11.2] x1 (dropped), [12.0, 12.1] x2; close 11.05
    assert m1.sr_zones(bars, n=9, k=1) == ([[10.0, 10.05, 2]], [[12.0, 12.1, 2]])
    assert m1.sr_zones(bars, n=10, k=1) is None


# --- envelope -------------------------------------------------------------------------------------------

def test_compute_steady_uptrend_is_complete_and_high_confidence():
    bars = bars_from([100 + i for i in range(260)], start=AS_OF - timedelta(days=259))
    nifty = {b.day: 1000.0 for b in bars}
    env = m1.compute("INE000000011", "TEST", bars, nifty, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    assert v["close"] == 359 and v["sma_20"] == pytest.approx(349.5) and v["sma_200"] == pytest.approx(259.5)
    assert v["rsi_14"] == 100.0 and v["trend"] == "up"
    assert v["rs_nifty_3m"] == pytest.approx(359 / 296 - 1)  # flat index: stock return over 63 sessions
    assert env.confidence.level == "high" and env.gaps == []
    assert any("RSI(14)" in f.reason for f in env.flags)
    assert env.as_of.date() == AS_OF and env.subject == "TEST"


def test_compute_short_history_reports_gaps():
    env = m1.compute("INE000000011", "TEST", bars_from([100.0] * 30, start=AS_OF - timedelta(days=29)), {}, AS_OF, NOW)
    ids = {m.id for m in env.metrics}
    assert "sma_20" in ids and "sma_50" not in ids and "high_52w" not in ids
    assert {g.item for g in env.gaps} >= {"50-day moving average", "200-day moving average", "52-week range"}
    assert env.confidence.level == "low"


def test_compute_withholds_history_before_corporate_action():
    closes = [200.0 + (i % 7) for i in range(300)] + [100.0 + (i % 7) for i in range(60)]  # 1:1 bonus at 300
    bars = bars_from(closes, start=AS_OF - timedelta(days=359))
    env = m1.compute("INE000000011", "TEST", bars, {}, AS_OF, NOW)
    ids = {m.id for m in env.metrics}
    assert "sma_50" in ids and "sma_200" not in ids
    assert any(f.severity == "watch" and str(bars[300].day) in f.reason for f in env.flags)
    assert any(g.item == "corporate actions" for g in env.gaps)


def test_compute_stale_and_empty():
    stale = m1.compute("INE000000011", "TEST", bars_from([100.0] * 30, start=date(2026, 1, 1)), {}, AS_OF, NOW)
    assert stale.confidence == m1.Confidence(level="low", reason="stale prices")
    empty = m1.compute("INE000000011", None, [], {}, AS_OF, NOW)
    assert empty.metrics == [] and empty.confidence.level == "low" and empty.subject == "INE000000011"


# --- run + logging (DB) ---------------------------------------------------------------------------------

def test_run_logs_ok_and_failed(db_conn):
    (sid,) = db_conn.execute(
        "INSERT INTO securities (isin, nse_symbol, name, last_seen) VALUES ('INE000000011', 'TEST', 'Test', %s) RETURNING id", (AS_OF,)
    ).fetchone()
    for b in bars_from([100 + i for i in range(260)], start=AS_OF - timedelta(days=259)):
        db_conn.execute("INSERT INTO prices_daily (security_id, exchange, trade_date, open, high, low, close, volume)"
                        " VALUES (%s, 'NSE', %s, %s, %s, %s, %s, 1)", (sid, b.day, b.open, b.high, b.low, b.close))
        db_conn.execute("INSERT INTO index_prices (index_name, trade_date, close) VALUES ('Nifty 50', %s, 1000)", (b.day,))

    env = m1.run(db_conn, m1.M1Input(subject="TEST", as_of=AS_OF))
    assert env.confidence.level == "high"
    (status, cost, envelope) = db_conn.execute(
        "SELECT r.status, r.cost_usd, o.envelope FROM agent_runs r JOIN agent_outputs o ON o.run_id = r.id"
    ).fetchone()
    assert status == "ok" and cost == 0 and m1.Envelope.model_validate(envelope) == env

    with pytest.raises(LookupError):
        m1.run(db_conn, m1.M1Input(subject="NOPE", as_of=AS_OF))
    assert db_conn.execute("SELECT status, error FROM agent_runs WHERE subject = 'NOPE'").fetchone() == (
        "failed", "LookupError: unknown security 'NOPE'")


# --- real data: RELIANCE across its 1:1 bonus (ex-date 2017-09-07) --------------------------------------

def load_reliance():
    rows = list(csv.DictReader(open(Path(__file__).parent / "fixtures/reliance_2017_2018.csv", newline="")))
    bars = [Bar(date.fromisoformat(r["date"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rows]
    nifty = {date.fromisoformat(r["date"]): float(r["nifty_close"]) for r in rows if r["nifty_close"]}
    return bars, nifty


def test_reliance_bonus_detected_and_matches_sql():
    bars, nifty = load_reliance()
    assert m1.corporate_action_breaks(bars) == [date(2017, 9, 7)]  # 1645.40 -> 818.10, and nothing else in the year
    env = m1.compute("INE002A01018", "RELIANCE", bars, nifty, date(2018, 3, 28), NOW)
    v = {m.id: m.value for m in env.metrics}
    # Expected values from Postgres window functions (AVG / row_number) over the 139 post-bonus sessions,
    # an implementation independent of this module.
    assert v["close"] == 882.7
    assert v["sma_20"] == pytest.approx(911.155, abs=1e-6)
    assert v["sma_50"] == pytest.approx(924.09, abs=1e-6)
    assert v["rs_nifty_3m"] == pytest.approx(-0.01143837, abs=1e-8)
    assert "rs_nifty_6m" in v and "sma_200" not in v and "high_52w" not in v and "rs_nifty_12m" not in v
    assert any(f.severity == "watch" and "2017-09-07" in f.reason for f in env.flags)
    assert env.confidence.level == "low" and env.confidence.reason == "only 139 usable sessions"
