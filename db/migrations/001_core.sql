-- Phase 1 core tables. Applied by Postgres initdb in dev (docker-compose.yml).
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Market data -------------------------------------------------------------

CREATE TABLE securities (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    isin        text NOT NULL UNIQUE,
    nse_symbol  text UNIQUE,
    bse_code    text UNIQUE,
    name        text NOT NULL,
    sector      text,
    industry    text,
    lot_size    integer CHECK (lot_size > 0),
    CHECK (nse_symbol IS NOT NULL OR bse_code IS NOT NULL)
);

-- Raw, unadjusted bhavcopy prices. Split/bonus adjustment comes with corporate_actions (M1 task).
CREATE TABLE prices_daily (
    security_id  bigint NOT NULL REFERENCES securities,
    exchange     text NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    trade_date   date NOT NULL,
    open         numeric(14, 4) NOT NULL,
    high         numeric(14, 4) NOT NULL,
    low          numeric(14, 4) NOT NULL,
    close        numeric(14, 4) NOT NULL,
    volume       bigint NOT NULL CHECK (volume >= 0),
    turnover     numeric(20, 2),
    PRIMARY KEY (security_id, exchange, trade_date),
    CHECK (low <= LEAST(open, close) AND high >= GREATEST(open, close))
);
SELECT create_hypertable('prices_daily', 'trade_date', chunk_time_interval => INTERVAL '1 year');

CREATE TABLE filings (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    security_id     bigint NOT NULL REFERENCES securities,
    exchange        text NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    filed_at        timestamptz NOT NULL,
    category        text NOT NULL,
    title           text NOT NULL,
    url             text NOT NULL UNIQUE,
    pdf_object_key  text
);
CREATE INDEX ON filings (security_id, filed_at DESC);

-- Long format so new XBRL line items need no schema change.
CREATE TABLE fundamentals_quarterly (
    security_id   bigint NOT NULL REFERENCES securities,
    period_end    date NOT NULL,
    period_type   text NOT NULL CHECK (period_type IN ('Q', 'A')),
    consolidated  boolean NOT NULL,
    item          text NOT NULL,
    value         numeric NOT NULL,
    unit          text NOT NULL,
    filing_id     bigint REFERENCES filings,
    PRIMARY KEY (security_id, period_end, period_type, consolidated, item)
);

-- Mutual funds ------------------------------------------------------------

CREATE TABLE fund_schemes (
    amfi_code  text PRIMARY KEY,
    name       text NOT NULL,
    amc        text NOT NULL,
    category   text NOT NULL,
    plan       text NOT NULL CHECK (plan IN ('direct', 'regular')),
    benchmark  text
);

CREATE TABLE fund_nav (
    amfi_code  text NOT NULL REFERENCES fund_schemes,
    nav_date   date NOT NULL,
    nav        numeric(18, 4) NOT NULL CHECK (nav > 0),
    PRIMARY KEY (amfi_code, nav_date)
);
SELECT create_hypertable('fund_nav', 'nav_date', chunk_time_interval => INTERVAL '1 year');

-- Monthly portfolio disclosure; isin may be a non-equity holding not in securities.
CREATE TABLE fund_holdings (
    amfi_code     text NOT NULL REFERENCES fund_schemes,
    month         date NOT NULL CHECK (month = date_trunc('month', month)),
    isin          text NOT NULL,
    weight_pct    numeric(7, 4) NOT NULL CHECK (weight_pct BETWEEN -100 AND 100),
    market_value  numeric(20, 2),
    PRIMARY KEY (amfi_code, month, isin)
);

-- Agents and reports ------------------------------------------------------

CREATE TABLE agent_runs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_id     text NOT NULL,
    subject      text NOT NULL,
    as_of        timestamptz NOT NULL,
    status       text NOT NULL CHECK (status IN ('running', 'ok', 'failed', 'timeout')),
    cost_usd     numeric(10, 6),
    duration_ms  integer,
    error        text,
    started_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON agent_runs (agent_id, subject, as_of DESC);

CREATE TABLE agent_outputs (
    run_id    bigint PRIMARY KEY REFERENCES agent_runs ON DELETE CASCADE,
    envelope  jsonb NOT NULL
);

CREATE TABLE reports (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject     text NOT NULL,
    as_of       date NOT NULL,
    report      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (subject, as_of)
);

CREATE TABLE verification_log (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_id  bigint NOT NULL REFERENCES reports ON DELETE CASCADE,
    run_id     bigint REFERENCES agent_runs,
    claim      text NOT NULL,
    metric_id  text,
    expected   text,
    actual     text,
    dropped    boolean NOT NULL,
    reason     text,
    logged_at  timestamptz NOT NULL DEFAULT now()
);
