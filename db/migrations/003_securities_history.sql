-- NSE bhavcopy history: symbols and ISINs change over time, and pre-July-2024 files carry no company name.
ALTER TABLE securities
    ALTER COLUMN name DROP NOT NULL,              -- filled when a UDiFF file names the security
    DROP CONSTRAINT securities_check,             -- a security whose symbol moved to a new ISIN keeps no symbol
    ADD COLUMN last_seen date;                    -- symbol/name only change from a file at least this recent

ALTER TABLE prices_daily
    ADD COLUMN symbol text,                       -- symbol as traded that day
    ADD COLUMN series text;                       -- EQ, BE, BZ, SM, ST
