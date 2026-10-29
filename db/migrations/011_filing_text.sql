-- Filing text for M4, and a per-filing LLM summary cache so the nightly run only pays for new/changed filings.
ALTER TABLE filings
    ADD COLUMN body_text   text,
    ADD COLUMN text_sha256 text;

CREATE TABLE filing_summaries (
    filing_id    bigint NOT NULL REFERENCES filings ON DELETE CASCADE,
    text_sha256  text NOT NULL,
    model        text NOT NULL,
    prompt_version text NOT NULL,
    summary      jsonb NOT NULL,  -- raw validated LLM output; quotes are re-verified on every use
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (filing_id, text_sha256, model, prompt_version)
);
