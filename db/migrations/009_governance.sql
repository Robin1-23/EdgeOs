-- Governance inputs for M3: quarterly shareholding pattern and corporate events (auditor changes).
CREATE TABLE shareholding (
    security_id           bigint NOT NULL REFERENCES securities,
    quarter_end           date NOT NULL,
    promoter_pct          numeric(7, 4) NOT NULL CHECK (promoter_pct BETWEEN 0 AND 100),  -- % of total shares
    promoter_pledged_pct  numeric(7, 4) NOT NULL CHECK (promoter_pledged_pct BETWEEN 0 AND 100),  -- % of promoter shares
    source                text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources,
    PRIMARY KEY (security_id, quarter_end)
);

CREATE TABLE corporate_events (
    security_id  bigint NOT NULL REFERENCES securities,
    event_date   date NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('auditor_resignation', 'auditor_appointment')),
    detail       text NOT NULL DEFAULT '',
    source       text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources,
    PRIMARY KEY (security_id, event_date, kind, detail)
);

-- "No auditor resignation" only means something if a source covered the company up to a date.
CREATE TABLE corporate_events_coverage (
    security_id    bigint PRIMARY KEY REFERENCES securities,
    covered_until  date NOT NULL,
    source         text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources
);
