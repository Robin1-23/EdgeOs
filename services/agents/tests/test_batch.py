import contextlib
from datetime import date, datetime, timedelta

from services.agents import batch
from services.agents.mother.tests.test_mother import AS_OF, acme, fake_agents, llm  # noqa: F401 (fixtures)
from services.pipelines import nightly

IST = batch.IST
D1, D2, D3 = date(2026, 10, 23), date(2026, 10, 26), date(2026, 10, 27)


def add_stock(conn, isin, symbol, days):
    """days: {date: (close, volume, turnover or None)}"""
    (sid,) = conn.execute("INSERT INTO securities (isin, nse_symbol, name) VALUES (%s, %s, %s) RETURNING id",
                          (isin, symbol, symbol)).fetchone()
    for d, (close, volume, turnover) in days.items():
        conn.execute("INSERT INTO prices_daily (security_id, exchange, trade_date, open, high, low, close, volume, turnover)"
                     " VALUES (%s, 'NSE', %s, %s, %s, %s, %s, %s, %s)", (sid, d, close, close, close, close, volume, turnover))


def add_fund(conn, code, scheme_type, nav_dates):
    conn.execute("INSERT INTO fund_schemes (amfi_code, name, amc, category, plan, scheme_type) VALUES (%s, %s, 'AMC', 'Equity', 'direct', %s)",
                 (code, f"Fund {code}", scheme_type))
    for d in nav_dates:
        conn.execute("INSERT INTO fund_nav (amfi_code, nav_date, nav) VALUES (%s, %s, 10)", (code, d))


def spend(conn, usd):
    conn.execute("INSERT INTO llm_calls (provider, model, tier, schema_name, request_hash, attempt, status, cost_usd)"
                 " VALUES ('test', 'm', 'specialist', 's', 'h', 1, 'ok', %s)", (usd,))


def fake_report(log, cost=0.0, fail=()):
    def report_fn(conn, kind, key, as_of, connect=None):
        log.append(f"{kind}:{key}")
        if key in fail:
            raise RuntimeError("source table empty")
        spend(conn, cost)
        return type("R", (), {"status": "published"})()
    return report_fn


def test_universe_by_average_traded_value(db_conn, monkeypatch):
    monkeypatch.setattr(batch, "LIQUIDITY_SESSIONS", 2)  # D2 and D3 count; D1 is outside the window
    add_stock(db_conn, "INE000000001", "BIGOLD", {D1: (100, 10**9, None)})  # liquid once, not lately -> excluded
    add_stock(db_conn, "INE000000002", "STEADY", {D2: (10, 1000, 30_000), D3: (10, 1000, 30_000)})  # avg 30k
    add_stock(db_conn, "INE000000003", "ONEDAY", {D3: (10, 5000, 50_000)})  # 50k on one day counts as 25k avg
    add_stock(db_conn, "INE000000004", "NOTURN", {D2: (20, 2000, None), D3: (20, 2000, None)})  # close x volume = 40k
    assert batch.stock_as_of(db_conn) == D3
    assert batch.stock_universe(db_conn, D3) == ["NOTURN", "STEADY", "ONEDAY"]
    assert batch.stock_universe(db_conn, D3, n=2) == ["NOTURN", "STEADY"]
    assert batch.stock_universe(db_conn, D2) == ["BIGOLD", "NOTURN", "STEADY"]  # as of D2 the window is D1, D2

    add_fund(db_conn, "200002", "open", [D3])
    add_fund(db_conn, "200001", "open", [D3])
    add_fund(db_conn, "200003", "close", [D3])  # closed-ended: not covered
    add_fund(db_conn, "200004", "open", [D2])  # no NAV on the latest date
    assert batch.fund_as_of(db_conn) == D3 and batch.fund_universe(db_conn, D3) == ["200001", "200002"]


def test_default_deadline_is_next_0630_ist():
    assert batch.default_deadline(datetime(2026, 10, 28, 0, 30, tzinfo=IST)) == datetime(2026, 10, 28, 6, 30, tzinfo=IST)
    assert batch.default_deadline(datetime(2026, 10, 28, 7, 0, tzinfo=IST)) == datetime(2026, 10, 29, 6, 30, tzinfo=IST)


def test_batch_runs_the_mother_and_resumes(acme, fake_agents, llm):  # noqa: F811
    add_stock(acme, "INE000000002", "STEADY", {AS_OF: (10, 1000, 10_000)})
    acme.execute("INSERT INTO prices_daily (security_id, exchange, trade_date, open, high, low, close, volume)"
                 " SELECT id, 'NSE', %s, 5, 5, 5, 5, 100 FROM securities WHERE nse_symbol = 'ACME'", (AS_OF,))
    connect = lambda: contextlib.nullcontext(acme)  # noqa: E731
    first = batch.run(acme, workers=1, connect=connect, budget_usd=10, deadline=datetime(2100, 1, 1, tzinfo=IST))
    assert first["status"] == "done" and first["stocks_as_of"] == AS_OF and first["funds_as_of"] is None
    assert first["subjects"]["stock"] == ["STEADY", "ACME"] and first["counts"] == {"published": 2}
    assert first["cost_usd"] > 0  # the scripted LLM's token counts, costed
    assert acme.execute("SELECT count(*) FROM reports WHERE status = 'published' AND as_of = %s", (AS_OF,)).fetchone()[0] == 2

    calls = len(llm.calls)
    again = batch.run(acme, workers=1, connect=connect, budget_usd=10, deadline=datetime(2100, 1, 1, tzinfo=IST))
    assert again["counts"] == {"already_published": 2} and len(llm.calls) == calls  # nothing rebuilt, nothing spent


def batch_conn(db_conn, n):
    for i in range(n):
        add_stock(db_conn, f"INE00000010{i}", f"S{i}", {D3: (10, 1000, 1000 * (n - i))})  # S0 most liquid
    return lambda: contextlib.nullcontext(db_conn)


def test_budget_stops_new_subjects(db_conn):
    connect, log = batch_conn(db_conn, 5), []
    out = batch.run(db_conn, workers=1, connect=connect, budget_usd=2.5, deadline=datetime(2100, 1, 1, tzinfo=IST),
                    report_fn=fake_report(log, cost=1.0))
    assert log == ["stock:S0", "stock:S1", "stock:S2"]  # spent 0, 1, 2 before each start; 3 >= 2.5 stops S3
    assert out["status"] == "partial" and out["stop_reason"] == "budget" and out["cost_usd"] == 3.0
    assert out["not_run"] == ["stock:S3", "stock:S4"] and out["counts"] == {"published": 3, "not_run": 2}


def test_deadline_stops_new_subjects(db_conn):
    connect, log = batch_conn(db_conn, 4), []
    clock = iter(datetime(2026, 10, 28, 6, 28, tzinfo=IST) + timedelta(minutes=m) for m in range(100))
    out = batch.run(db_conn, workers=1, connect=connect, budget_usd=100, deadline=datetime(2026, 10, 28, 6, 30, tzinfo=IST),
                    report_fn=fake_report(log), now=lambda: next(clock))
    # clock reads: 06:28 at start (deadline is given, so not read), then 06:28, 06:29, 06:30 before each subject
    assert log == ["stock:S0", "stock:S1"] and out["stop_reason"] == "deadline" and out["not_run"] == ["stock:S2", "stock:S3"]


def test_one_failure_does_not_stop_the_batch(db_conn):
    connect, log = batch_conn(db_conn, 3), []
    out = batch.run(db_conn, workers=1, connect=connect, budget_usd=100, deadline=datetime(2100, 1, 1, tzinfo=IST),
                    report_fn=fake_report(log, fail={"S1"}))
    assert log == ["stock:S0", "stock:S1", "stock:S2"] and out["status"] == "done"
    assert out["counts"] == {"published": 2, "failed": 1} and out["failed"] == {"stock:S1": "RuntimeError: source table empty"}


def test_limit_and_no_data(db_conn):
    connect, log = batch_conn(db_conn, 3), []
    out = batch.run(db_conn, workers=1, connect=connect, limit=1, deadline=datetime(2100, 1, 1, tzinfo=IST), report_fn=fake_report(log))
    assert log == ["stock:S0"] and out["subjects"] == {"stock": ["S0"], "fund": []}


def test_empty_database_builds_nothing(db_conn):
    out = batch.run(db_conn, workers=1, deadline=datetime(2100, 1, 1, tzinfo=IST), report_fn=fake_report([]))
    assert out["status"] == "done" and out["counts"] == {} and out["stocks_as_of"] is None


def test_nightly_logs_the_batch_after_the_data(db_conn):
    from services.pipelines.tests.fakes import FakeAdapter

    seen = []
    results = nightly.nightly(db_conn, D3, FakeAdapter(), reports=lambda c: seen.append(len(results_so_far(c))) or {"status": "partial", "counts": {}})
    assert results[-1]["pipeline"] == "report_batch" and results[-1]["status"] == "partial"
    assert seen == [len(results) - 1]  # every data step was logged before the batch started
    assert nightly.exit_code(results) == 0


def results_so_far(conn):
    return conn.execute("SELECT id FROM pipeline_runs WHERE pipeline <> 'report_batch'").fetchall()
