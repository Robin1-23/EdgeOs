-- One row per LLM request attempt; agent_runs.cost_usd is the sum over the run's calls.
CREATE TABLE llm_calls (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          bigint REFERENCES agent_runs ON DELETE CASCADE,  -- NULL outside an agent run
    provider        text NOT NULL,
    model           text NOT NULL,
    tier            text NOT NULL,
    schema_name     text NOT NULL,
    request_hash    text NOT NULL,  -- sha256 of model + messages + schema; never the API key
    attempt         integer NOT NULL,
    status          text NOT NULL CHECK (status IN ('ok', 'invalid_output', 'error')),
    input_tokens    integer NOT NULL DEFAULT 0,
    cached_tokens   integer NOT NULL DEFAULT 0,
    output_tokens   integer NOT NULL DEFAULT 0,
    reasoning_tokens integer NOT NULL DEFAULT 0,
    cost_usd        numeric(12, 6) NOT NULL DEFAULT 0,
    latency_ms      integer,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON llm_calls (run_id);
