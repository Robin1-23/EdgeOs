-- Pro subscriptions, written only from verified Razorpay webhooks; the API derives the plan from here.
CREATE TABLE subscriptions (
    provider_subscription_id  text PRIMARY KEY,           -- Razorpay sub_...
    user_id                   text NOT NULL,              -- Firebase uid (set in the subscription's notes at creation)
    status                    text NOT NULL,              -- created | authenticated | active | pending | halted | cancelled | completed | ...
    current_end               timestamptz,                -- end of the paid period
    cancel_at_period_end      boolean NOT NULL DEFAULT false,
    event_at                  timestamptz,                -- created_at of the latest webhook applied (older ones are ignored)
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON subscriptions (user_id);

-- Webhook deliveries already applied (Razorpay retries and may deliver twice).
CREATE TABLE billing_events (
    event_id     text PRIMARY KEY,
    received_at  timestamptz NOT NULL DEFAULT now()
);
