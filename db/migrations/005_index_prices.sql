-- NSE daily index closes (ind_close_all_DDMMYYYY.csv). Some indices (VIX, dividend points) publish only a close.
CREATE TABLE index_prices (
    index_name   text NOT NULL,
    trade_date   date NOT NULL,
    open         numeric(14, 4),
    high         numeric(14, 4),
    low          numeric(14, 4),
    close        numeric(14, 4) NOT NULL CHECK (close > 0),
    volume       bigint,
    turnover_cr  numeric(16, 2),
    pe           numeric(10, 2),
    pb           numeric(10, 2),
    div_yield    numeric(8, 2),
    PRIMARY KEY (index_name, trade_date),
    CHECK (open IS NULL OR (low <= LEAST(open, close) AND high >= GREATEST(open, close)))
);
SELECT create_hypertable('index_prices', 'trade_date', chunk_time_interval => INTERVAL '5 years');

ALTER TABLE pipeline_runs DROP CONSTRAINT pipeline_runs_pipeline_check,
    ADD CONSTRAINT pipeline_runs_pipeline_check CHECK (pipeline IN ('amfi_nav', 'nse_bhavcopy', 'nse_indices'));
