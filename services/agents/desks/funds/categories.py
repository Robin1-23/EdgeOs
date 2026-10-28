"""AMFI category strings -> SEBI (Oct 2017) categories, and category -> benchmark index.

AMFI files spell one category several ways ("Equity Scheme - Large Cap Fund" / "Equity Schemes - Large Cap Fund").
Only exact equivalents are mapped; pre-2018 names ("Income/Debt Oriented Schemes - Short Term Fund") and
index funds / ETFs / FoFs stay unmapped, which MF2 reports as a gap rather than guessing a peer group.
"""

# Suffix after "<group> - " -> canonical SEBI category.
SEBI_CATEGORY = {
    # equity
    "Large Cap Fund": "Large Cap", "Mid Cap Fund": "Mid Cap", "Small Cap Fund": "Small Cap",
    "Large & Mid Cap Fund": "Large & Mid Cap", "Multi Cap Fund": "Multi Cap", "Flexi Cap Fund": "Flexi Cap",
    "Focused Fund": "Focused", "Value Fund": "Value", "Contra Fund": "Contra", "Dividend Yield Fund": "Dividend Yield",
    "ELSS": "ELSS", "ELSS- Tax Saver Fund": "ELSS",
    "Sectoral/ Thematic": "Sectoral/Thematic", "Sectoral Fund": "Sectoral/Thematic", "Thematic Fund": "Sectoral/Thematic",
    # hybrid
    "Aggressive Hybrid Fund": "Aggressive Hybrid", "Arbitrage Fund": "Arbitrage", "Balanced Hybrid Fund": "Balanced Hybrid",
    "Conservative Hybrid Fund": "Conservative Hybrid", "Equity Savings": "Equity Savings", "Equity Savings Fund": "Equity Savings",
    "Multi Asset Allocation": "Multi Asset Allocation", "Multi Asset Allocation Fund": "Multi Asset Allocation",
    "Dynamic Asset Allocation or Balanced Advantage": "Balanced Advantage",
    "Balanced Advantage Fund/ Dynamic Asset Allocation": "Balanced Advantage",
    # debt (only names identical to SEBI's)
    "Overnight Fund": "Overnight", "Liquid Fund": "Liquid", "Money Market Fund": "Money Market",
    "Ultra Short Duration Fund": "Ultra Short Duration", "Low Duration Fund": "Low Duration",
    "Short Duration Fund": "Short Duration", "Medium Duration Fund": "Medium Duration",
    "Medium to Long Duration Fund": "Medium to Long Duration", "Long Duration Fund": "Long Duration",
    "Dynamic Bond": "Dynamic Bond", "Corporate Bond Fund": "Corporate Bond", "Credit Risk Fund": "Credit Risk",
    "Banking and PSU Fund": "Banking and PSU", "Banking and PSU Debt Fund": "Banking and PSU", "Gilt Fund": "Gilt",
    "10-year Constant Maturity Gilt Fund": "Gilt 10Y Constant Duration",
    # solution oriented
    "Retirement Fund": "Retirement", "Children’s Fund": "Children's", "Childrens' Fund": "Children's",
}
GROUPS = ("Equity Scheme", "Hybrid Scheme", "Debt Scheme", "Income/Debt Oriented Schemes", "Solution Oriented Scheme",
          "Children’s Fund")

# Category -> price index in index_prices, following SEBI/AMFI tier-1 benchmarks (which are TRI; NSE's daily
# file has only the price version, so MF2 labels these "price index, not TRI").
# Needs a human check against AMFI's tier-1 benchmark list before launch.
BENCHMARK = {
    "Large Cap": "Nifty 100", "Mid Cap": "Nifty Midcap 150", "Small Cap": "Nifty Smallcap 250",
    "Large & Mid Cap": "NIFTY LargeMidcap 250", "Flexi Cap": "Nifty 500", "ELSS": "Nifty 500", "Focused": "Nifty 500",
    "Value": "Nifty 500", "Contra": "Nifty 500", "Dividend Yield": "Nifty 500",
    # Multi Cap's tier-1 is Nifty 500 Multicap 50:25:25, not in the NSE file -> no benchmark (gap)
}


def sebi_category(raw: str | None) -> str | None:
    if not raw:
        return None
    group, sep, suffix = raw.partition(" - ")
    group = group.replace("Schemes **", "Scheme").replace("Schemes", "Scheme").strip()
    if not sep or not any(group == g.replace("Schemes", "Scheme") for g in GROUPS):
        return None
    return SEBI_CATEGORY.get(suffix.strip())


def is_growth(option: str | None) -> bool:
    """Growth option only: IDCW NAVs drop on each payout, so their NAV returns understate the fund."""
    return option is not None and "growth" in option.lower() and "idcw" not in option.lower() and "dividend" not in option.lower()
