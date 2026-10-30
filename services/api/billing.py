"""Pro plan billing through Razorpay Subscriptions: create and cancel subscriptions, and apply verified webhooks.

Card and UPI details go only to Razorpay's checkout; EdgeOS stores subscription ids and states. The plan a user is on
is derived from `subscriptions`, which only verified webhooks write.
Test mode only until the legal review (SPEC: SEBI Research Analyst question): live keys are refused unless
BILLING_LIVE=1. Env: RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET, RAZORPAY_PLAN_ID (the Pro plan
created in the Razorpay dashboard).
"""

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime

import psycopg

API = "https://api.razorpay.com/v1"
PRO_STATES = ("active", "pending")  # pending = renewal payment being retried: keep access until current_end


class BillingError(Exception):
    pass


def config() -> dict | None:
    """Razorpay settings, or None when billing is not configured. Raises on live keys without BILLING_LIVE=1."""
    keys = {k: os.environ.get(f"RAZORPAY_{k.upper()}") for k in ("key_id", "key_secret", "webhook_secret", "plan_id")}
    if not all(keys.values()):
        return None
    if not keys["key_id"].startswith("rzp_test_") and os.environ.get("BILLING_LIVE") != "1":
        raise BillingError("live Razorpay keys are refused until BILLING_LIVE=1 (after the legal review)")
    return keys


def call(cfg: dict, method: str, path: str, body: dict | None = None) -> dict:
    """One Razorpay REST call with basic auth (key id and secret); errors become BillingError."""
    auth = base64.b64encode(f"{cfg['key_id']}:{cfg['key_secret']}".encode()).decode()
    req = urllib.request.Request(f"{API}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read() or b"{}").get("error", {}).get("description", e.reason)
        raise BillingError(f"razorpay {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise BillingError(f"razorpay unreachable: {e.reason}") from e


def is_pro(conn: psycopg.Connection, uid: str) -> bool:
    row = conn.execute("SELECT 1 FROM subscriptions WHERE user_id = %s AND status = ANY(%s) AND current_end > now() LIMIT 1",
                       (uid, list(PRO_STATES))).fetchone()
    return row is not None


def current(conn: psycopg.Connection, uid: str) -> dict | None:
    """The user's latest subscription, for the account page."""
    row = conn.execute("SELECT provider_subscription_id, status, current_end, cancel_at_period_end FROM subscriptions"
                       " WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1", (uid,)).fetchone()
    return dict(zip(("id", "status", "current_end", "cancel_at_period_end"), row)) if row else None


def subscribe(conn: psycopg.Connection, cfg: dict, uid: str) -> dict:
    """Create a monthly Pro subscription; the browser completes it in Razorpay's checkout. The uid goes in notes so
    webhooks can be matched to the user."""
    sub = call(cfg, "POST", "/subscriptions", {"plan_id": cfg["plan_id"], "total_count": 120, "customer_notify": 1,
                                               "notes": {"uid": uid}})
    conn.execute("INSERT INTO subscriptions (provider_subscription_id, user_id, status) VALUES (%s, %s, %s)"
                 " ON CONFLICT DO NOTHING", (sub["id"], uid, sub.get("status", "created")))
    return {"key_id": cfg["key_id"], "subscription_id": sub["id"]}


def cancel(conn: psycopg.Connection, cfg: dict, uid: str) -> None:
    """Cancel at the end of the paid period (access continues until then)."""
    sub = current(conn, uid)
    if sub is None or sub["status"] not in (*PRO_STATES, "authenticated", "created"):
        raise BillingError("no subscription to cancel")
    call(cfg, "POST", f"/subscriptions/{sub['id']}/cancel", {"cancel_at_cycle_end": 1})
    conn.execute("UPDATE subscriptions SET cancel_at_period_end = true, updated_at = now() WHERE provider_subscription_id = %s",
                 (sub["id"],))


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Razorpay signs the raw webhook body: hex HMAC-SHA256 with the webhook secret."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def apply_webhook(conn: psycopg.Connection, event_id: str, payload: dict) -> str:
    """Apply one verified subscription.* event. Duplicates and events older than the last one applied are ignored."""
    if not event_id or conn.execute("INSERT INTO billing_events (event_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING 1",
                                    (event_id,)).fetchone() is None:
        return "duplicate"
    entity = (payload.get("payload", {}).get("subscription") or {}).get("entity")
    if not payload.get("event", "").startswith("subscription.") or not entity:
        return "ignored"
    uid = (entity.get("notes") or {}).get("uid")
    if not uid:
        return "ignored"  # not created by EdgeOS
    event_at = datetime.fromtimestamp(payload.get("created_at", 0), UTC)
    current_end = datetime.fromtimestamp(entity["current_end"], UTC) if entity.get("current_end") else None
    row = conn.execute(
        """INSERT INTO subscriptions (provider_subscription_id, user_id, status, current_end, event_at) VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (provider_subscription_id) DO UPDATE SET status = EXCLUDED.status,
               current_end = coalesce(EXCLUDED.current_end, subscriptions.current_end),
               event_at = EXCLUDED.event_at, updated_at = now()
           WHERE subscriptions.event_at IS NULL OR subscriptions.event_at <= EXCLUDED.event_at
           RETURNING 1""", (entity["id"], uid, entity["status"], current_end, event_at)).fetchone()
    return "applied" if row else "stale"
