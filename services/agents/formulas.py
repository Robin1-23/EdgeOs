"""Formula catalogue: a plain-English definition for every formula_id an agent can put on a metric, so every number
on a page links to how it was computed (SPEC acceptance criterion). Keyed by (agent_id, formula_id before any ':'):
'item:revenue_ops', 'cache:M2' and 'llm_tone:m4-v1' look up 'item', 'cache' and 'llm_tone'.
The root conftest fails the test run if an agent emits a formula_id that is missing here.
"""

FORMULAS: dict[tuple[str, str], str] = {
    # M1 price and technicals (daily NSE closes; prices are not adjusted, so windows stop at a split/bonus break)
    ("M1", "close"): "Closing price on the latest trading day.",
    ("M1", "sma_20"): "Simple moving average: the mean of the last 20 daily closes.",
    ("M1", "sma_50"): "Simple moving average: the mean of the last 50 daily closes.",
    ("M1", "sma_200"): "Simple moving average: the mean of the last 200 daily closes.",
    ("M1", "rsi_wilder_14"): "Relative strength index over 14 sessions with Wilder smoothing: 100 - 100 / (1 + average "
                             "gain / average loss), seeded with the simple average of the first 14 gains and losses.",
    ("M1", "macd_12_26_9"): "MACD line = 12-session EMA - 26-session EMA of closes; signal = 9-session EMA of the MACD "
                            "line; histogram = line - signal. EMAs use alpha 2/(n+1), seeded with an n-session average.",
    ("M1", "range_252"): "Highest high and lowest low over the last 252 sessions (about 52 weeks), and the close's "
                         "position in that range: (close - low) / (high - low), 0 at the low and 1 at the high.",
    ("M1", "rel_return_63"): "Return relative to the Nifty 50 over 63 sessions: (1 + stock return) / (1 + index return) - 1.",
    ("M1", "rel_return_126"): "Return relative to the Nifty 50 over 126 sessions: (1 + stock return) / (1 + index return) - 1.",
    ("M1", "rel_return_252"): "Return relative to the Nifty 50 over 252 sessions: (1 + stock return) / (1 + index return) - 1.",
    ("M1", "trend_sma_50_200"): "Trend state: 'up' if close > 50-day average > 200-day average, 'down' if close < "
                                "50-day < 200-day, otherwise 'mixed'.",
    ("M1", "sr_pivots_252"): "Price zones where swing highs or lows (the extreme of 5 sessions either side) cluster "
                             "within 1.5% at least twice in 252 sessions; the two nearest below and above the close.",
    # M2 fundamentals (consolidated statements when the latest year has them, else standalone; INR crore)
    ("M2", "item"): "A line item as reported in the financial statements for that period.",
    ("M2", "cagr_3"): "Compound annual growth over 3 years: (end / start) ^ (1/3) - 1; shown only when both are positive.",
    ("M2", "cagr_5"): "Compound annual growth over 5 years: (end / start) ^ (1/5) - 1; shown only when both are positive.",
    ("M2", "cagr_10"): "Compound annual growth over 10 years: (end / start) ^ (1/10) - 1; shown only when both are positive.",
    ("M2", "ttm_yoy"): "Trailing twelve months = sum of the last 4 quarters; growth = that sum / the 4 quarters before "
                       "- 1. Needs 8 consecutive quarters and a positive base.",
    ("M2", "ebitda"): "Operating EBITDA = profit before tax - exceptional items + finance cost + depreciation - other income.",
    ("M2", "ebitda_margin"): "Operating EBITDA / revenue from operations, same year.",
    ("M2", "pat_margin"): "Profit after tax / revenue from operations, same year.",
    ("M2", "roe_avg"): "Return on equity = profit attributable to owners / average of opening and closing owners' equity.",
    ("M2", "roce_avg"): "Return on capital employed = EBIT (profit before tax - exceptional items + finance cost) / average "
                        "of opening and closing capital employed (total equity + all borrowings).",
    ("M2", "debt_equity"): "Total borrowings (current + non-current) / total equity at year-end.",
    ("M2", "cash_conversion_3y"): "Operating cash flow over the last 3 years / profit after tax over the same 3 years.",
    ("M2", "fcf"): "Free cash flow = cash flow from operations - capital expenditure, same year.",
    ("M2", "growth_quality_v1"): "Rule checks, each pass or fail: revenue rose in at least 4 of 5 years; 3-year cash "
                                 "conversion at least 0.8; ROCE at least 12%; debt/equity at most 1; 5-year profit and "
                                 "revenue growth within 10 points of each other. The score is the share passed, shown "
                                 "when at least 3 checks can be evaluated. A description, not a rating.",
    # M3 forensic red flags
    ("M3", "item"): "A figure as reported in the quarterly shareholding pattern.",
    ("M3", "diff_1y"): "Change in percentage points between the latest quarter and the same quarter a year earlier.",
    ("M3", "diff_3y"): "Change in percentage points between the latest quarter and the same quarter three years earlier.",
    ("M3", "count_3y"): "Number of matching corporate events filed in the last 3 years.",
    ("M3", "cash_conversion_3y"): "Operating cash flow over the last 3 years / profit after tax over the same 3 years.",
    ("M3", "contingent_to_networth"): "Contingent liabilities / owners' equity at the latest year-end.",
    ("M3", "dilution_3y"): "Growth in shares outstanding over 3 year-ends, multiplying only the year-on-year ratios that do "
                           "not match a split or bonus ratio (those steps are counted separately).",
    ("M3", "red_flags_v1"): "Sum of the weights of the checks that triggered (weights total 100): promoter pledge at "
                            "least 20% (15); pledge up 5+ points in a year (15); promoter holding down 5+ points in 3 "
                            "years (10); auditor resignation in 3 years (20); 3-year cash flow below half of profit "
                            "(20); contingent liabilities above half of net worth (10); share count up over 10% in 3 "
                            "years excluding split/bonus (10). Shown with the weight that could be evaluated.",
    # M4 filings and concalls (labels from fixed lists; every statement is checked against a verbatim quote)
    ("M4", "llm_event_tag"): "Event type label chosen by a language model from a fixed list, kept only with a quote "
                             "found verbatim in the filing.",
    ("M4", "llm_tone"): "Management tone label (from a fixed list) chosen by a language model, kept only with a quote "
                        "found verbatim in the filing.",
    ("M4", "tone_change"): "Tone label of the previous results filing or concall, then of the latest one.",
    # M5 news and sentiment
    ("M5", "count_7d"): "Headlines naming the company in the 7 days to the report date.",
    ("M5", "count_30d"): "Headlines naming the company in the 30 days to the report date.",
    ("M5", "count_prev_30d"): "Headlines naming the company in the 30 days before the latest 30.",
    ("M5", "distinct_30d"): "Distinct publishers with a headline naming the company in the last 30 days.",
    ("M5", "coverage_change_30d"): "Headline count in the last 30 days / the 30 days before - 1.",
    ("M5", "count_label"): "Labelled headlines in the last 30 days with this tone label.",
    ("M5", "net_sentiment"): "(positive - negative headlines) / labelled headlines in the last 30 days, from -1 to 1.",
    ("M5", "llm_label"): "Tone and type labels chosen by a language model from fixed lists for one headline.",
    ("M5", "headline"): "A headline naming the company, shown without a label.",
    # M6 peers and sector
    ("M6", "peer_group"): "Companies in the same industry (or sector, when the industry has too few members).",
    ("M6", "cache"): "The company's own value, read from its latest fundamentals (M2) or valuation (M7) result.",
    ("M6", "peer_median"): "Median of the peers' values for the same metric.",
    ("M6", "peer_percentile"): "Share of peers with a strictly lower value: 0 = lowest in the group, 1 = highest.",
    # M7 valuation (multiples on the latest close; never a target price)
    ("M7", "mcap"): "Market capitalisation = latest close x shares outstanding.",
    ("M7", "ev"): "Enterprise value = market cap + total borrowings - cash and equivalents.",
    ("M7", "pe"): "Price to earnings = market cap / profit attributable to owners (trailing 12 months when 8 quarters "
                  "exist, else the latest year); shown only when profit is positive.",
    ("M7", "pb"): "Price to book = market cap / owners' equity at the latest year-end.",
    ("M7", "ev_ebitda"): "Enterprise value / operating EBITDA; shown only when EBITDA is positive.",
    ("M7", "pe_band_10y"): "Lowest, median and highest year-end P/E over 10 years, and the share of those readings below today's.",
    ("M7", "pb_band_10y"): "Lowest, median and highest year-end P/B over 10 years, and the share of those readings below today's.",
    ("M7", "ev_ebitda_band_10y"): "Lowest, median and highest year-end EV/EBITDA over 10 years, and the share of those "
                                  "readings below today's.",
    ("M7", "pe_peers"): "Median P/E of same-industry peers, the share of peers with a lower P/E, and the peer count.",
    ("M7", "pb_peers"): "Median P/B of same-industry peers, the share of peers with a lower P/B, and the peer count.",
    ("M7", "ev_ebitda_peers"): "Median EV/EBITDA of same-industry peers, the share of peers with a lower value, and the "
                               "peer count.",
    ("M7", "reverse_dcf"): "The yearly profit growth over 10 years at which discounted profits (then 5% terminal growth) "
                           "equal today's market cap, at the stated discount rate. An implied assumption, not a forecast.",
    # MF1 fund X-ray
    ("MF1", "count"): "Number of positions in the scheme's latest monthly portfolio.",
    ("MF1", "overlap"): "Sum over stocks held by both schemes of the smaller of the two portfolio weights; also the "
                        "count of stocks held by both.",
    ("MF1", "sum"): "Total of the amounts entered.",
    ("MF1", "effective_exposure"): "Amount-weighted average of each scheme's weight in a stock or sector, as a share of "
                                   "the combined holding.",
    ("MF1", "ter"): "Total expense ratio as last disclosed for the scheme, per year.",
    ("MF1", "ter_gap"): "Regular-plan expense ratio - direct-plan expense ratio of the same scheme.",
    ("MF1", "cost_gap_10"): "Amount x [(1 + g - direct TER)^10 - (1 + g - regular TER)^10] at the stated gross growth g: "
                            "the extra cost of the regular plan over 10 years. An illustration, not a forecast.",
    ("MF1", "cost_gap_20"): "Amount x [(1 + g - direct TER)^20 - (1 + g - regular TER)^20] at the stated gross growth g: "
                            "the extra cost of the regular plan over 20 years. An illustration, not a forecast.",
    # MF2 returns and consistency
    ("MF2", "nav"): "Net asset value per unit on the latest NAV date.",
    ("MF2", "cagr_1"): "1-year return: NAV now / NAV a year earlier - 1.",
    ("MF2", "cagr_3"): "3-year annualised return: (NAV now / NAV 3 years earlier) ^ (1/3) - 1.",
    ("MF2", "cagr_5"): "5-year annualised return: (NAV now / NAV 5 years earlier) ^ (1/5) - 1.",
    ("MF2", "category_mean_1"): "Mean 1-year return of schemes in the same category and plan over the same window.",
    ("MF2", "category_mean_3"): "Mean 3-year annualised return of schemes in the same category and plan over the same window.",
    ("MF2", "category_mean_5"): "Mean 5-year annualised return of schemes in the same category and plan over the same window.",
    ("MF2", "rolling_beat_1"): "Across month-end rolling 1-year windows: the share where the scheme beat its category "
                               "mean, and the median and worst scheme return.",
    ("MF2", "rolling_beat_3"): "Across month-end rolling 3-year windows: the share where the scheme beat its category "
                               "mean, and the median and worst scheme return.",
    ("MF2", "rolling_beat_5"): "Across month-end rolling 5-year windows: the share where the scheme beat its category "
                               "mean, and the median and worst scheme return.",
    ("MF2", "downside_capture_36"): "Over the months in the last 36 when the benchmark fell: geometric mean scheme return "
                                    "/ geometric mean benchmark return. 1 = fell as much as the benchmark.",
    ("MF2", "downside_capture_60"): "Over the months in the last 60 when the benchmark fell: geometric mean scheme return "
                                    "/ geometric mean benchmark return. 1 = fell as much as the benchmark.",
    # MF3 closet-indexer check
    ("MF3", "active_share"): "Half the sum of |scheme weight - benchmark weight| over every stock in either; cash counts "
                             "as one off-benchmark position. 0 = identical to the benchmark, 1 = no overlap.",
    ("MF3", "count"): "Number of positions in the scheme's latest monthly portfolio.",
    ("MF3", "overlap_weight"): "Share of the scheme's portfolio held in benchmark constituents.",
    ("MF3", "tracking_error_36"): "Sample standard deviation of monthly (scheme - benchmark) returns over 36 months x √12.",
    ("MF3", "ter"): "Total expense ratio as last disclosed for the scheme, per year.",
    ("MF3", "active_fee"): "Expense ratio / active share: the yearly cost per unit of portfolio that differs from the benchmark.",
}


def describe(agent_id: str, formula_id: str) -> str | None:
    return FORMULAS.get((agent_id, formula_id.split(":", 1)[0]))
