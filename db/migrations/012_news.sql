-- News headlines (headline + link + publisher only, per typical licence terms; no article bodies) and per-company
-- LLM labels, cached so each headline is labelled once per company.
CREATE TABLE news_items (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    published_at  timestamptz NOT NULL,
    publisher     text NOT NULL,
    headline      text NOT NULL CHECK (length(headline) BETWEEN 1 AND 400),
    url           text NOT NULL UNIQUE,
    source        text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources
);
CREATE INDEX ON news_items (published_at DESC);

CREATE TABLE news_labels (
    news_id         bigint NOT NULL REFERENCES news_items ON DELETE CASCADE,
    security_id     bigint NOT NULL REFERENCES securities,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    sentiment       text NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    event_tag       text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (news_id, security_id, model, prompt_version)
);
