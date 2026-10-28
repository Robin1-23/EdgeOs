from datetime import date, datetime

import pytest

from services.agents.desks.markets import m7_valuation as m7
from services.agents.desks.markets.tests.test_m2_fundamentals import ISIN, REVENUE, acme_records, rows_of
from services.pipelines import fundamentals

AS_OF = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 1, 0, tzinfo=m7.IST)
# Year-end close = revenue x f, with 2 crore shares and PAT = revenue / 10  ->  year-end P/E = 20 x f
F = dict(zip(range(2017, 2027), [1.0, 1.1, 0.9, 1.2, 0.8, 1.0, 1.05, 0.95, 1.15, 1.25]))


def valuation_records():
    recs = acme_records()
    for y in range(2016, 2027):
        fy = date(y, 3, 31)
        recs.append((ISIN, fy, "A", True, "shares_outstanding", 2))
        if y < 2025:  # FY25/FY26 equity comes from the M2 fixture (80, 100)
            recs.append((ISIN, fy, "A", True, "equity_owners", REVENUE[y] * 0.5))
    recs.append((ISIN, date(2026, 3, 31), "A", True, "cash_and_equivalents", 20))
    return rows_of(recs)


def prices(today_close=300.0):
    return [(date(y, 3, 31), REVENUE[y] * f) for y, f in F.items()] + [(AS_OF, today_close)]


# --- formulas, hand-worked ------------------------------------------------------------------------------

def test_multiples():
    bs = {"shares_outstanding": 10, "equity_owners": 100, "borrowings_current": 10, "borrowings_noncurrent": 30,
          "cash_and_equivalents": 20}
    # mcap 50 x 10 = 500; P/E 500/20 = 25; P/B 500/100 = 5; EV 500 + 40 - 20 = 520; EV/EBITDA 520/36
    assert m7.multiples(bs, 20, 36, 50) == {"mcap": 500, "pe": 25, "pb": 5, "ev": 520, "ev_ebitda": pytest.approx(14.444444, abs=1e-6)}
    assert "pe" not in m7.multiples(bs, -5, 36, 50)  # loss: P/E not meaningful
    assert m7.multiples({}, 20, 36, 50) == {}  # no share count, nothing


def test_band():
    assert m7.band([10, 20, 30, 40, 50], 35) == {"min": 10, "median": 30, "max": 50, "points": 5, "percentile": 0.6}


def test_reverse_dcf_one_year():
    # e0 10 grows 10% for 1 year -> 11; PV 11/1.12 = 9.821429; terminal 11 x 1.05 / (0.12 - 0.05) / 1.12 = 147.321429
    assert m7.dcf_value(10, 0.10, 0.12, years=1) == pytest.approx(157.142857, abs=1e-6)
    assert m7.implied_growth(157.142857, 10, 0.12, years=1) == pytest.approx(0.10, abs=1e-6)
    assert m7.implied_growth(100, -1, 0.12) is None  # loss-making
    assert m7.implied_growth(1, 10, 0.12) is None  # would need < -50% growth


# --- envelope -------------------------------------------------------------------------------------------

def test_compute_multiples_history_peers_and_implied_growth():
    rows = valuation_records()
    peers = {f"INE00000010{i}": (rows, prices(p)) for i, p in enumerate((150.0, 200.0, 450.0))}
    env = m7.compute(ISIN, "ACME", rows, prices(), peers, AS_OF, NOW)
    v = {m.id: m.value for m in env.metrics}
    # today: mcap 300 x 2 = 600; TTM PAT (5+5+6+6) = 22 -> P/E 27.2727; P/B 600/100 = 6; EV 600+40-20 = 620 / EBITDA 36
    assert v["mcap"] == 600 and v["pe"] == pytest.approx(600 / 22) and v["pb"] == 6
    assert v["ev_ebitda"] == pytest.approx(620 / 36)
    # year-end P/E FY17..FY26 = 20 x F: 20 22 18 24 16 20 21 19 23 25
    assert (v["pe_hist_min"], v["pe_hist_median"], v["pe_hist_max"], v["pe_hist_points"]) == (16, 20.5, 25, 10)
    assert v["pe_hist_percentile"] == 1.0
    assert any("P/E is above its 10-year" in f.reason for f in env.flags)
    # peers at 150 / 200 / 450 -> P/E 13.64 / 18.18 / 40.91; median 18.18; 2 of 3 below ACME
    assert v["pe_peer_median"] == pytest.approx(400 / 22) and v["pe_peer_percentile"] == pytest.approx(2 / 3)
    g11, g12, g13 = v["implied_growth_r11"], v["implied_growth_r12"], v["implied_growth_r13"]
    assert g11 < g12 < g13 and m7.dcf_value(22, g12, 0.12) == pytest.approx(600, rel=1e-6)
    assert {g.item for g in env.gaps} == {"EV/EBITDA history band"}  # only FY26 has debt/cash/EBITDA inputs
    assert env.confidence.level == "medium"
    for m in env.metrics:  # SPEC: no target price / fair value anywhere
        assert "target" not in m.name.lower() and "fair" not in m.name.lower()


def test_withholds_when_a_split_follows_the_share_count():
    px = prices()[:-1] + [(date(2026, 6, 1), 500.0), (date(2026, 6, 2), 250.0), (AS_OF, 300.0)]  # 1:1 bonus in June
    env = m7.compute(ISIN, "ACME", valuation_records(), px, {}, AS_OF, NOW)
    assert env.metrics == [] and any(f.severity == "watch" and "2026-06-02" in f.reason for f in env.flags)


def test_stale_price_and_missing_shares():
    stale = m7.compute(ISIN, "ACME", valuation_records(), prices()[:-1], {}, AS_OF, NOW)
    assert stale.confidence.level == "low" and "no close within 7 days" in stale.gaps[0].reason
    no_shares = m7.compute(ISIN, "ACME", rows_of(acme_records()), prices(), {}, AS_OF, NOW)
    assert "shares_outstanding" in no_shares.gaps[0].reason


# --- run (DB) -------------------------------------------------------------------------------------------

def test_run_with_industry_peers(db_conn):
    rows = valuation_records()
    for i, (isin, symbol, close) in enumerate(((ISIN, "ACME", 300.0), ("INE000000102", "P1", 150.0),
                                               ("INE000000110", "P2", 200.0), ("INE000000128", "P3", 450.0))):
        (sid,) = db_conn.execute("INSERT INTO securities (isin, nse_symbol, name, industry, classification_source, last_seen)"
                                 " VALUES (%s, %s, %s, 'Widgets', 'dev_local', %s)"
                                 " RETURNING id", (isin, symbol, symbol, AS_OF)).fetchone()
        for d, c in prices(close):
            db_conn.execute("INSERT INTO prices_daily (security_id, exchange, trade_date, open, high, low, close, volume)"
                            " VALUES (%s, 'NSE', %s, %s, %s, %s, %s, 1)", (sid, d, c, c, c, c))
        recs = [(isin, pe, pt, c, item, v) for pe, pt, c, item, v in rows]
        fundamentals.load(db_conn, fundamentals.build(recs))
    env = m7.run(db_conn, m7.M7Input(subject="ACME", as_of=AS_OF))
    v = {m.id: m.value for m in env.metrics}
    assert v["pe_peer_count"] == 3 and v["pe_peer_median"] == pytest.approx(400 / 22)
    assert {s.id: s.providers for s in env.sources} == {"valuation": ["dev_local"], "peers": ["dev_local"]}
    assert db_conn.execute("SELECT public_ok FROM agent_outputs").fetchone() == (False,)
