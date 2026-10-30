-- The ask-box quota counts attempts, recorded before the run starts, so a run that fails mid-way still counts.
CREATE TABLE ask_attempts (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON ask_attempts (user_id, created_at);
