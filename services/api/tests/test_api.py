from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from services.api import auth, main

IST = main.IST
D1, D2 = date(2026, 10, 26), date(2026, 10, 27)
LIC = "licensed_test"  # a source whose licence allows display


@pytest.fixture
def client(db_conn, monkeypatch):
    monkeypatch.delenv("SERVE_DEV_DATA", raising=False)
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    db_conn.execute("INSERT INTO data_sources (name, public_ok, description) VALUES (%s, true, 'test licence')", (LIC,))
    main.app.dependency_overrides[main.get_conn] = lambda: db_conn
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def dev(monkeypatch):
    monkeypatch.setenv("SERVE_DEV_DATA", "1")


def stock(conn, isin, symbol, name, source=LIC, bars=()):
    """bars: [(date, close)]"""
    (sid,) = conn.execute("INSERT INTO securities (isin, nse_symbol, name, source) VALUES (%s, %s, %s, %s) RETURNING id",
                          (isin, symbol, name, source)).fetchone()
    for d, c in bars:
        conn.execute("INSERT INTO prices_daily (security_id, exchange, trade_date, open, high, low, close, volume, source)"
                     " VALUES (%s, 'NSE', %s, %s, %s, %s, %s, 100, %s)", (sid, d, c, c, c, c, source))


def report(conn, subject, as_of, public_ok, status="published", agents=()):
    """agents: [(agent_id, key, envelope dict, public_ok)] stored as ok runs for as_of."""
    conn.execute("INSERT INTO reports (subject, as_of, kind, status, public_ok, report) VALUES (%s, %s, %s, %s, %s, %s)",
                 (subject, as_of, subject.split(":")[0], status, public_ok, Jsonb({"summary": [{"text": "s", "cites": []}]})))
    for agent_id, key, env, pub in agents:
        (rid,) = conn.execute("INSERT INTO agent_runs (agent_id, subject, as_of, status) VALUES (%s, %s, %s, 'ok') RETURNING id",
                              (agent_id, key, datetime.combine(as_of, datetime.min.time(), IST))).fetchone()
        conn.execute("INSERT INTO agent_outputs (run_id, envelope, public_ok) VALUES (%s, %s, %s)", (rid, Jsonb(env), pub))


def env(agent_id, formula_id):
    return {"agent_id": agent_id, "metrics": [{"id": "x", "name": "x", "value": 0.2, "unit": "ratio", "formula_id": formula_id}]}


# --- licence gate ---------------------------------------------------------------------------------------

def test_stock_page_licence_gate(client, db_conn, monkeypatch):
    stock(db_conn, "INE000000001", "ACME", "Acme Widgets Limited")
    report(db_conn, "stock:ACME", D2, public_ok=False, agents=[("M2", "ACME", env("M2", "roe_avg"), False)])
    r = client.get("/api/stocks/ACME")
    assert r.status_code == 404 and r.json()["detail"] == main.NOT_LICENSED

    dev(monkeypatch)
    r = client.get("/api/stocks/acme")  # symbol is case-insensitive
    body = r.json()
    assert r.status_code == 200 and body["dev_only"] is True and r.headers["cache-control"] == "private, no-store"
    assert body["agents"]["M2"]["envelope"]["metrics"][0]["formula"].startswith("Return on equity")
    assert body["agents"]["M1"] == {"available": False, "reason": "no result for this date"}


def test_public_report_is_served_and_cached(client, db_conn):
    stock(db_conn, "INE000000001", "ACME", "Acme Widgets Limited")
    report(db_conn, "stock:ACME", D1, public_ok=True, agents=[("M2", "ACME", env("M2", "roe_avg"), True)])
    report(db_conn, "stock:ACME", D2, public_ok=True, status="blocked")  # a blocked report is never served
    r = client.get("/api/stocks/ACME")
    assert r.status_code == 200 and r.json()["dev_only"] is False and r.json()["as_of"] == str(D1)
    assert r.headers["cache-control"] == main.CACHE_PUBLIC
    assert client.get("/api/stocks/NOPE").status_code == 404


def test_unlicensed_agent_output_is_withheld(client, db_conn):
    stock(db_conn, "INE000000001", "ACME", "Acme Widgets Limited")
    report(db_conn, "stock:ACME", D2, public_ok=True, agents=[("M2", "ACME", env("M2", "roe_avg"), False)])
    assert client.get("/api/stocks/ACME").json()["agents"]["M2"] == {"available": False, "reason": main.NOT_LICENSED}


def test_prices_only_from_licensed_rows(client, db_conn, monkeypatch):
    stock(db_conn, "INE000000001", "ACME", "Acme", bars=[(D1, 10), (D2, 11)])
    stock(db_conn, "INE000000002", "DEVCO", "Devco", source="dev_local", bars=[(D2, 5)])
    r = client.get("/api/stocks/ACME/prices", params={"from": "2026-10-27"})
    assert r.status_code == 200 and [b["close"] for b in r.json()["bars"]] == [11.0] and r.json()["sources"] == [LIC]
    assert client.get("/api/stocks/DEVCO/prices", params={"from": "2026-01-01"}).status_code == 404
    dev(monkeypatch)
    r = client.get("/api/stocks/DEVCO/prices", params={"from": "2026-01-01"})
    assert r.status_code == 200 and r.json()["dev_only"] is True and r.json()["sources"] == ["dev_local"]


def test_dev_switch_refused_outside_dev_local(monkeypatch):
    monkeypatch.setenv("SERVE_DEV_DATA", "1")
    monkeypatch.setenv("DATA_SOURCE", "licensed_vendor")
    with pytest.raises(RuntimeError, match="only with DATA_SOURCE=dev_local"):
        with TestClient(main.app):
            pass
    monkeypatch.setenv("DATA_SOURCE", "dev_local")
    main.check_config()  # allowed


def test_fund_page_has_scheme_details(client, db_conn):
    db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, option, scheme_type, source)"
                    " VALUES ('100001', 'Alpha Flexi Cap', 'Alpha AMC', 'Equity Scheme - Flexi Cap Fund', 'direct', 'Growth', 'open', %s)", (LIC,))
    report(db_conn, "fund:100001", D2, public_ok=True, agents=[("MF2", "100001", env("MF2", "cagr_3"), True)])
    body = client.get("/api/funds/100001").json()
    assert body["meta"] == {"name": "Alpha Flexi Cap", "amc": "Alpha AMC", "category": "Equity Scheme - Flexi Cap Fund",
                            "plan": "direct", "option": "Growth"}
    assert body["agents"]["MF2"]["envelope"]["metrics"][0]["formula"].startswith("3-year annualised return")
    assert body["agents"]["MF1"] == {"available": False, "reason": "no result for this date"}
    assert client.get("/api/funds/999999").status_code == 404


def test_stock_page_has_company_details(client, db_conn):
    stock(db_conn, "INE000000001", "ACME", "Acme Widgets Limited")
    stock(db_conn, "INE000000002", "DEVCO", "Devco", source="dev_local")
    report(db_conn, "stock:ACME", D2, public_ok=True)
    report(db_conn, "stock:DEVCO", D2, public_ok=True)
    assert client.get("/api/stocks/ACME").json()["meta"] == {
        "name": "Acme Widgets Limited", "isin": "INE000000001", "symbol": "ACME", "sector": None, "industry": None}
    assert client.get("/api/stocks/DEVCO").json()["meta"] is None  # details from an unlicensed source are left out


def test_rebase():
    assert main.rebase([(D1, 50.0), (D2, 55.0)], 50.0) == [{"date": D1, "value": 100.0}, {"date": D2, "value": 110.0}]


def test_fund_series_rebased_with_benchmark(client, db_conn):
    db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, option, scheme_type, source) VALUES"
                    " ('100001', 'Alpha Flexi Cap', 'AMC', 'Equity Scheme - Flexi Cap Fund', 'direct', 'Growth', 'open', %(s)s),"
                    " ('100002', 'Alpha Index', 'AMC', 'Other Scheme - Index Funds', 'direct', 'Growth', 'open', %(s)s)", {"s": LIC})
    for code in ("100001", "100002"):
        for d, v in ((D1, 20.0), (D2, 21.0)):
            db_conn.execute("INSERT INTO fund_nav (amfi_code, nav_date, nav, source) VALUES (%s, %s, %s, %s)", (code, d, v, LIC))
    # Flexi Cap -> Nifty 500; its base is the close on or before the fund's first date (23 Oct, a Friday)
    for d, c in ((date(2026, 10, 23), 20000.0), (D2, 20500.0)):
        db_conn.execute("INSERT INTO index_prices (index_name, trade_date, close, source) VALUES ('Nifty 500', %s, %s, %s)", (d, c, LIC))
    body = client.get("/api/funds/100001/series", params={"from": "2026-10-01", "to": "2026-10-27"}).json()
    assert [(p["date"], p["value"]) for p in body["fund"]] == [(str(D1), 100.0), (str(D2), 105.0)]
    assert body["benchmark"]["name"] == "Nifty 500" and "not total return" in body["benchmark"]["label"]
    assert [(p["date"], p["value"]) for p in body["benchmark"]["points"]] == [(str(D2), 102.5)]
    assert client.get("/api/funds/100002/series", params={"from": "2026-10-01"}).json()["benchmark"] is None  # unmapped category
    assert client.get("/api/funds/100001/series", params={"from": "2027-01-01"}).status_code == 404


# --- search and home ------------------------------------------------------------------------------------

def test_search_orders_exact_then_prefix_then_name(client, db_conn):
    stock(db_conn, "INE000000001", "TATAPOWER", "Tata Power Company Limited")
    stock(db_conn, "INE000000002", "TATA", "Tata Holdings Limited")
    stock(db_conn, "INE000000003", "VOLTAS", "Voltas Limited (a Tata company)")
    stock(db_conn, "INE000000004", "TATAHIDE", "Hidden Tata Limited", source="dev_local")  # not licensed: not listed
    db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, scheme_type, source) VALUES"
                    " ('100001', 'Tata Flexi Cap', 'AMC', 'Equity', 'direct', 'open', %(s)s),"
                    " ('100002', 'Tata FMP Series 9', 'AMC', 'Debt', 'direct', 'close', %(s)s)", {"s": LIC})
    body = client.get("/api/search", params={"q": "tata"}).json()
    assert [s["symbol"] for s in body["stocks"]] == ["TATA", "TATAPOWER", "VOLTAS"]
    assert [f["amfi_code"] for f in body["funds"]] == ["100001"]  # closed-ended not listed
    assert client.get("/api/search", params={"q": "INE000000003"}).json()["stocks"][0]["symbol"] == "VOLTAS"
    assert client.get("/api/search", params={"q": "_"}).json()["stocks"] == []  # LIKE wildcards are escaped
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_home_indices_and_breadth(client, db_conn):
    for name, closes in (("India VIX", (14.0, 12.6)), ("Nifty 50", (22000.0, 22220.0)), ("Nifty Alpha", (1.0, 2.0))):
        for d, c in zip((D1, D2), closes):
            db_conn.execute("INSERT INTO index_prices (index_name, trade_date, close, source) VALUES (%s, %s, %s, %s)", (name, d, c, LIC))
    stock(db_conn, "INE000000001", "UP", "Up", bars=[(D1, 10), (D2, 11)])
    stock(db_conn, "INE000000002", "DOWN", "Down", bars=[(D1, 10), (D2, 9)])
    stock(db_conn, "INE000000003", "FLAT", "Flat", bars=[(D1, 10), (D2, 10)])
    stock(db_conn, "INE000000004", "NEW", "New", bars=[(D2, 10)])  # no previous close: not counted
    body = client.get("/api/home").json()
    assert [(i["name"], i["close"], round(i["change"], 4)) for i in body["indices"]] == [("Nifty 50", 22220.0, 0.01), ("India VIX", 12.6, -0.1)]
    assert body["breadth"]["date"] == str(D2)
    assert (body["breadth"]["advances"], body["breadth"]["declines"], body["breadth"]["unchanged"]) == (1, 1, 1)


def test_formulas_endpoint(client):
    body = client.get("/api/formulas").json()
    assert body["M1"]["rsi_wilder_14"].startswith("Relative strength index") and set(body) >= {"M1", "MF3"}


# --- overlap --------------------------------------------------------------------------------------------

def test_overlap_runs_mf1(client, db_conn, monkeypatch):
    for code, holdings in (("100001", {"INE000000001": 50, "INE000000002": 50}), ("200001", {"INE000000001": 25, "INE000000003": 75})):
        db_conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, option, scheme_type)"
                        " VALUES (%s, %s, 'AMC', 'Equity Scheme - Flexi Cap Fund', 'direct', 'Growth', 'open')", (code, f"Fund {code}"))
        for isin, w in holdings.items():
            db_conn.execute("INSERT INTO fund_holdings (amfi_code, month, isin, weight_pct) VALUES (%s, '2026-09-01', %s, %s)", (code, isin, w))
    req = {"funds": [{"amfi_code": "100001", "amount": 60000}, {"amfi_code": "200001", "amount": 40000}], "as_of": "2026-10-27"}
    assert client.post("/api/overlap", json=req).status_code == 404  # dev_local holdings: not licensed for display
    dev(monkeypatch)
    body = client.post("/api/overlap", json=req).json()
    v = {m["id"]: m for m in body["envelope"]["metrics"]}
    assert body["dev_only"] is True and v["overlap_100001_200001"]["value"] == pytest.approx(0.25)  # min(50, 25) of A
    assert v["overlap_100001_200001"]["formula"].startswith("Sum over stocks held by both")
    assert client.post("/api/overlap", json={"funds": [{"amfi_code": "100001"}]}).status_code == 422  # needs two
    dup = {"funds": [{"amfi_code": "100001"}, {"amfi_code": "100001"}]}
    assert client.post("/api/overlap", json=dup).status_code == 422


# --- ask ------------------------------------------------------------------------------------------------

@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(auth, "firebase_app", lambda: object())
    monkeypatch.setattr(auth, "verify", lambda token, app: {"good-token": "user-1"}[token])
    return {"Authorization": "Bearer good-token"}


def fake_ask(db_conn, public_ok=True, agents=None, fail=False):
    class R:
        def __init__(self):
            self.public_ok, self.blocked_reason = public_ok, None
            self.agents = agents if agents is not None else {"M2 ACME": "ok"}

        def model_dump(self, mode):
            return {"summary": [{"text": "answer", "cites": ["M2:roe"]}]}

    def ask(conn, question, as_of, connect):
        if fail:
            raise RuntimeError("the planner crashed")
        (aid,) = conn.execute("INSERT INTO ask_answers (question, as_of, plan, status) VALUES (%s, %s, '{}', 'answered') RETURNING id",
                              (question, as_of)).fetchone()
        return {"id": aid, "status": "answered", "report": R()}
    return ask


def test_ask_needs_firebase_and_a_valid_token(client, monkeypatch, signed_in):
    monkeypatch.setattr(auth, "firebase_app", lambda: None)
    assert client.post("/api/ask", json={"question": "How has ACME grown?"}, headers=signed_in).status_code == 503
    monkeypatch.setattr(auth, "firebase_app", lambda: object())
    assert client.post("/api/ask", json={"question": "How has ACME grown?"}).status_code == 401
    assert client.get("/api/ask/quota").status_code == 401
    bad = {"Authorization": "Bearer forged"}
    r = client.post("/api/ask", json={"question": "How has ACME grown?"}, headers=bad)
    assert r.status_code == 401 and r.json()["detail"] == "sign-in token is invalid or expired"


def test_ask_answers_and_enforces_the_daily_quota(client, db_conn, monkeypatch, signed_in):
    monkeypatch.setattr(main.orchestrator, "ask", fake_ask(db_conn))
    monkeypatch.setattr(main, "ASK_DAILY_LIMIT", 2)
    assert client.get("/api/ask/quota", headers=signed_in).json() == {"limit": 2, "remaining": 2}
    r = client.post("/api/ask", json={"question": "How has ACME grown?"}, headers=signed_in)
    assert r.status_code == 200 and r.json()["answer"]["summary"][0]["text"] == "answer" and r.json()["remaining"] == 1
    assert r.headers["cache-control"] == "private, no-store"
    assert db_conn.execute("SELECT user_id FROM ask_answers").fetchall() == [("user-1",)]
    assert client.post("/api/ask", json={"question": "And margins?"}, headers=signed_in).json()["remaining"] == 0
    r = client.post("/api/ask", json={"question": "One more?"}, headers=signed_in)
    assert r.status_code == 429 and "resets at midnight IST" in r.json()["detail"]
    assert client.get("/api/ask/quota", headers=signed_in).json()["remaining"] == 0
    assert client.post("/api/ask", json={"question": "x" * 501}, headers=signed_in).status_code == 422


def test_a_crashed_run_still_counts_against_the_quota(client, db_conn, monkeypatch, signed_in):
    monkeypatch.setattr(main.orchestrator, "ask", fake_ask(db_conn, fail=True))
    with pytest.raises(RuntimeError, match="planner crashed"):
        client.post("/api/ask", json={"question": "How has ACME grown?"}, headers=signed_in)
    assert client.get("/api/ask/quota", headers=signed_in).json()["remaining"] == main.ASK_DAILY_LIMIT - 1


def test_ask_returns_the_evidence_with_citation_prefixes(client, db_conn, monkeypatch, signed_in):
    today = datetime.now(IST).date()
    stock(db_conn, "INE000000001", "ACME", "Acme")
    report(db_conn, "stock:ACME", today, public_ok=True, agents=[("M2", "ACME", env("M2", "roe_avg"), True),
                                                                 ("M2", "BETA", env("M2", "roe_avg"), False)])
    monkeypatch.setattr(main.orchestrator, "ask", fake_ask(db_conn))
    ev = client.post("/api/ask", json={"question": "How has ACME grown?"}, headers=signed_in).json()["evidence"]
    assert [(e["prefix"], e["available"]) for e in ev] == [("M2", True)]  # one subject: ids are "M2:..."
    assert ev[0]["envelope"]["metrics"][0]["formula"].startswith("Return on equity")

    monkeypatch.setattr(main.orchestrator, "ask", fake_ask(db_conn, agents={"M2 ACME": "ok", "M2 BETA": "ok"}))
    ev = client.post("/api/ask", json={"question": "Compare ACME and BETA"}, headers=signed_in).json()["evidence"]
    assert [(e["prefix"], e["available"], e.get("reason")) for e in ev] == [
        ("ACME/M2", True, None), ("BETA/M2", False, main.NOT_LICENSED)]  # several subjects: "ACME/M2:..."


def test_planner_failure_is_reported_without_internals(client, monkeypatch, signed_in):
    def ask(conn, question, as_of, connect):
        return {"id": 1, "status": "needs_clarification", "message": f"{main.planner.PLAN_FAILED}: openai 401: AuthenticationError"}
    monkeypatch.setattr(main.orchestrator, "ask", ask)
    body = client.post("/api/ask", json={"question": "How has ACME grown?"}, headers=signed_in).json()
    assert body["status"] == "failed" and "openai" not in body["message"].lower()


def test_ask_answer_from_unlicensed_data_is_withheld(client, db_conn, monkeypatch, signed_in):
    monkeypatch.setattr(main.orchestrator, "ask", fake_ask(db_conn, public_ok=False))
    r = client.post("/api/ask", json={"question": "How has ACME grown?"}, headers=signed_in)
    assert r.status_code == 404 and r.json()["detail"] == main.NOT_LICENSED
