-- Sector/industry comes from its own feed; record which source set it (peer groups inherit its public_ok).
ALTER TABLE securities ADD COLUMN classification_source text REFERENCES data_sources;
CREATE INDEX ON securities (industry) WHERE industry IS NOT NULL;
