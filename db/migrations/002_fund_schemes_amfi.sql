-- AMFI NAVAll.txt columns. ~40% of rows (old FMPs, closed-ended series) carry no plan, so plan is nullable.
ALTER TABLE fund_schemes
    ALTER COLUMN plan DROP NOT NULL,
    ADD COLUMN isin_growth   text,
    ADD COLUMN isin_reinvest text,
    ADD COLUMN option        text,
    ADD COLUMN scheme_type   text CHECK (scheme_type IN ('open', 'close', 'interval'));

-- AMFI publishes up to 8 decimals (e.g. 10.0087785); 4 would round and break idempotent upserts.
ALTER TABLE fund_nav ALTER COLUMN nav TYPE numeric(20, 8);
