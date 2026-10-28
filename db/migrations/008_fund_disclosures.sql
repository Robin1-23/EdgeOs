-- Benchmark index constituent weights and scheme expense ratios (MF1/MF3). fund_holdings exists since 001.
CREATE TABLE index_constituents (
    index_name  text NOT NULL,
    month       date NOT NULL CHECK (month = date_trunc('month', month)),
    isin        text NOT NULL,
    weight_pct  numeric(7, 4) NOT NULL CHECK (weight_pct > 0 AND weight_pct <= 100),
    source      text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources,
    PRIMARY KEY (index_name, month, isin)
);

CREATE TABLE fund_expense_ratios (
    amfi_code  text NOT NULL REFERENCES fund_schemes,
    as_of      date NOT NULL,
    ter_pct    numeric(6, 4) NOT NULL CHECK (ter_pct >= 0 AND ter_pct < 10),  -- total expense ratio, % a year
    source     text NOT NULL DEFAULT 'dev_local' REFERENCES data_sources,
    PRIMARY KEY (amfi_code, as_of)
);
