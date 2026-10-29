-- Mother agent outputs: page reports (one per subject per day) and ask-box answers.
ALTER TABLE reports
    ADD COLUMN kind      text NOT NULL DEFAULT 'stock' CHECK (kind IN ('stock', 'fund')),
    ADD COLUMN status    text NOT NULL DEFAULT 'blocked' CHECK (status IN ('published', 'blocked')),
    ADD COLUMN public_ok boolean NOT NULL DEFAULT false;  -- every agent output behind it is public_ok

ALTER TABLE verification_log ADD COLUMN agent_id text;

CREATE TABLE ask_answers (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question    text NOT NULL,
    as_of       date NOT NULL,
    plan        jsonb NOT NULL,
    answer      jsonb,
    status      text NOT NULL CHECK (status IN ('answered', 'needs_clarification', 'unsupported', 'blocked')),
    public_ok   boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- verification needs a row to point at even for ask answers
ALTER TABLE verification_log ALTER COLUMN report_id DROP NOT NULL,
    ADD COLUMN ask_id bigint REFERENCES ask_answers ON DELETE CASCADE;
