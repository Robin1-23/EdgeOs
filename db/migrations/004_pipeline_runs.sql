-- One row per pipeline run per target day; written as 'running' first so a crash leaves a trace.
CREATE TABLE pipeline_runs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pipeline     text NOT NULL CHECK (pipeline IN ('amfi_nav', 'nse_bhavcopy')),
    target_date  date NOT NULL,
    status       text NOT NULL CHECK (status IN ('running', 'loaded', 'no_file', 'failed')),
    started_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at  timestamptz,
    duration_ms  integer,
    summary      jsonb,
    error        text
);
CREATE INDEX ON pipeline_runs (pipeline, target_date) WHERE status = 'loaded';
