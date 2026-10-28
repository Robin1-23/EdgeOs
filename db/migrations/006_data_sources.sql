-- Provenance: every data row names the source it came from, and only sources whose licence allows public
-- display may reach public pages. Defaults fail closed (dev_local, not public).
CREATE TABLE data_sources (
    name         text PRIMARY KEY,
    public_ok    boolean NOT NULL DEFAULT false,
    description  text NOT NULL
);
INSERT INTO data_sources (name, public_ok, description) VALUES
    ('dev_local', false, 'Files downloaded from NSE/AMFI websites during development. Their terms of use forbid '
                         'public display, redistribution and bulk storage; dev and testing only.'),
    ('licensed_vendor', false, 'Licensed feed (e.g. Accord, CMOTS, NSE Data & Analytics). Set public_ok = true only '
                               'once the signed licence permits display on the website.');

ALTER TABLE securities             ADD COLUMN source text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources;
ALTER TABLE prices_daily           ADD COLUMN source text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources;
ALTER TABLE index_prices           ADD COLUMN source text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources;
ALTER TABLE fund_schemes           ADD COLUMN source text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources;
ALTER TABLE fund_nav               ADD COLUMN source text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources;
ALTER TABLE fund_holdings          ADD COLUMN source text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources;
ALTER TABLE fundamentals_quarterly ADD COLUMN source text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources;
ALTER TABLE filings                ADD COLUMN source text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources;

-- An agent output is publishable only if every source it read is public_ok (set by services/agents/runlog.py).
ALTER TABLE agent_outputs ADD COLUMN public_ok boolean NOT NULL DEFAULT false;
