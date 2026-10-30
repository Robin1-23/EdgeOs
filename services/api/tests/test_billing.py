import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.api import auth, billing, main

SECRET = "whsec_test"
CFG = {"key_id": "rzp_test_abc", "key_secret": "secret", "webhook_secret": SECRET, "plan_id": "plan_pro"}


@pytest.fixture
def client(db_conn, monkeypatch):
    for k, v in CFG.items():
        monkeypatch.setenv(f"RAZORPAY_{k.upper()}", v)
    monkeypatch.delenv("BILLING_LIVE", raising=False)
    monkeypatch.setattr(auth, "firebase_app", lambda: object())
    monkeypatch.setattr(auth, "verify", lambda token, app: {"tok-1": "user-1", "tok-2": "user-2"}[token])
    main.app.dependency_overrides[main.get_conn] = lambda: db_conn
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


U1, U2 = {"Authorization": "Bearer tok-1"}, {"Authorization": "Bearer tok-2"}


def event(name, sub_id="sub_1", status="active", uid="user-1", current_end=None, created_at=None):
    end = current_end or datetime.now(UTC) + timedelta(days=30)
    return {"event": name, "created_at": created_at or int(time.time()),
            "payload": {"subscription": {"entity": {"id": sub_id, "status": status, "current_end": int(end.timestamp()),
                                                    "notes": {"uid": uid}}}}}


def deliver(client, payload, event_id, secret=SECRET):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/billing/webhook", content=body,
                       headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"})


def test_signature_check():
    body = b'{"event":"subscription.activated"}'
    good = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert billing.verify_signature(body, good, "s3cret")
    assert not billing.verify_signature(body + b" ", good, "s3cret")
    assert not billing.verify_signature(body, "", "s3cret")


def test_webhook_rejects_bad_signatures(client):
    assert deliver(client, event("subscription.activated"), "evt_1", secret="wrong").status_code == 400
    assert client.get("/api/ask/quota", headers=U1).json()["plan"] == "free"


def test_activation_makes_pro_and_halt_ends_it(client):
    assert client.get("/api/ask/quota", headers=U1).json() == {"plan": "free", "limit": main.FREE_LIMIT, "remaining": main.FREE_LIMIT}
    t = int(time.time())
    assert deliver(client, event("subscription.activated", created_at=t), "evt_1").json() == {"result": "applied"}
    assert client.get("/api/ask/quota", headers=U1).json()["plan"] == "pro"
    assert client.get("/api/ask/quota", headers=U2).json()["plan"] == "free"  # someone else's subscription
    assert deliver(client, event("subscription.activated", created_at=t), "evt_1").json() == {"result": "duplicate"}
    assert deliver(client, event("subscription.pending", status="pending", created_at=t + 10), "evt_2").json()["result"] == "applied"
    assert client.get("/api/ask/quota", headers=U1).json()["plan"] == "pro"  # renewal being retried: access kept
    assert deliver(client, event("subscription.halted", status="halted", created_at=t + 20), "evt_3").json()["result"] == "applied"
    assert client.get("/api/ask/quota", headers=U1).json()["plan"] == "free"


def test_stale_and_foreign_events_are_ignored(client):
    t = int(time.time())
    deliver(client, event("subscription.halted", status="halted", created_at=t), "evt_1")
    assert deliver(client, event("subscription.activated", created_at=t - 60), "evt_0").json()["result"] == "stale"  # arrived late
    assert client.get("/api/ask/quota", headers=U1).json()["plan"] == "free"
    foreign = event("subscription.activated", sub_id="sub_x")
    foreign["payload"]["subscription"]["entity"]["notes"] = {}
    assert deliver(client, foreign, "evt_9").json()["result"] == "ignored"  # not created by EdgeOS
    assert deliver(client, {"event": "payment.captured", "payload": {}}, "evt_8").json()["result"] == "ignored"


def test_expired_period_is_free(client):
    past = datetime.now(UTC) - timedelta(days=1)
    deliver(client, event("subscription.activated", current_end=past), "evt_1")
    assert client.get("/api/ask/quota", headers=U1).json()["plan"] == "free"


def test_subscribe_and_cancel_call_razorpay(client, db_conn, monkeypatch):
    calls = []

    def fake_call(cfg, method, path, body=None):
        calls.append((method, path, body))
        return {"id": "sub_1", "status": "created"}
    monkeypatch.setattr(billing, "call", fake_call)
    r = client.post("/api/billing/subscribe", headers=U1)
    assert r.json() == {"key_id": "rzp_test_abc", "subscription_id": "sub_1"}
    assert calls[0] == ("POST", "/subscriptions", {"plan_id": "plan_pro", "total_count": 120, "customer_notify": 1,
                                                   "notes": {"uid": "user-1"}})
    deliver(client, event("subscription.activated"), "evt_1")
    assert client.post("/api/billing/subscribe", headers=U1).status_code == 409  # already Pro
    assert client.post("/api/billing/cancel", headers=U1).json()["subscription"]["cancel_at_period_end"] is True
    assert calls[-1] == ("POST", "/subscriptions/sub_1/cancel", {"cancel_at_cycle_end": 1})
    assert client.get("/api/ask/quota", headers=U1).json()["plan"] == "pro"  # paid period continues
    assert client.post("/api/billing/cancel", headers=U2).status_code == 409


def test_razorpay_errors_reach_the_page_without_details(client, monkeypatch):
    def fail(*a, **k):
        raise billing.BillingError("razorpay 401: Authentication failed")
    monkeypatch.setattr(billing, "call", fail)
    r = client.post("/api/billing/subscribe", headers=U1)
    assert r.status_code == 502 and "nothing was charged" in r.json()["detail"] and "401" not in r.json()["detail"]


def test_billing_refuses_live_keys_and_missing_config(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abc")
    assert client.post("/api/billing/subscribe", headers=U1).status_code == 503
    with pytest.raises(billing.BillingError, match="BILLING_LIVE"):
        billing.config()
    monkeypatch.setenv("BILLING_LIVE", "1")
    assert billing.config()["key_id"] == "rzp_live_abc"
    monkeypatch.delenv("RAZORPAY_PLAN_ID")
    assert billing.config() is None and client.post("/api/billing/subscribe", headers=U1).status_code == 503


def ask_row(conn, uid, question, status="answered", days_ago=0, answer=None):
    (aid,) = conn.execute(
        "INSERT INTO ask_answers (question, as_of, plan, answer, status, user_id, created_at) VALUES (%s, current_date, '{}', %s, %s, %s,"
        " now() - make_interval(days => %s)) RETURNING id",
        (question, json.dumps(answer or {"summary": [], "agents": {}}), status, uid, days_ago)).fetchone()
    return aid


def test_account_history_by_plan_and_reopening(client, db_conn, monkeypatch):
    monkeypatch.setenv("SERVE_DEV_DATA", "1")  # stored test answers are not public_ok
    recent = ask_row(db_conn, "user-1", "Recent?")
    ask_row(db_conn, "user-1", "Two months ago?", days_ago=60)
    other = ask_row(db_conn, "user-2", "Someone else's?")
    failed = ask_row(db_conn, "user-1", "Failed?", status="needs_clarification",
                     answer={"message": "could not plan the question: openai 401: AuthenticationError"})
    acct = client.get("/api/account", headers=U1).json()
    assert acct["plan"] == "free" and acct["history_days"] == 30 and acct["billing_available"] is True
    assert {h["question"]: h["status"] for h in acct["history"]} == {"Recent?": "answered", "Failed?": "failed"}  # 30 days on free
    deliver(client, event("subscription.activated"), "evt_1")
    assert {h["question"] for h in client.get("/api/account", headers=U1).json()["history"]} == {"Recent?", "Failed?", "Two months ago?"}

    assert client.get(f"/api/ask/{recent}", headers=U1).json()["question"] == "Recent?"
    assert client.get(f"/api/ask/{other}", headers=U1).status_code == 404  # never another user's answer
    reopened = client.get(f"/api/ask/{failed}", headers=U1).json()
    assert reopened["status"] == "failed" and "openai" not in reopened["message"]


def test_delete_account(client, db_conn, monkeypatch):
    ask_row(db_conn, "user-1", "Mine?")
    ask_row(db_conn, "user-2", "Theirs?")
    db_conn.execute("INSERT INTO ask_attempts (user_id) VALUES ('user-1')")
    deliver(client, event("subscription.activated"), "evt_1")
    assert client.delete("/api/account", headers=U1).status_code == 409  # cancel Pro first
    monkeypatch.setattr(billing, "call", lambda *a, **k: {})
    client.post("/api/billing/cancel", headers=U1)
    assert client.delete("/api/account", headers=U1).json() == {"deleted": {"answers": 1, "attempts": 1}}
    assert db_conn.execute("SELECT user_id FROM ask_answers").fetchall() == [("user-2",)]
    assert db_conn.execute("SELECT count(*) FROM subscriptions WHERE user_id = 'user-1'").fetchone() == (1,)  # kept for accounting
