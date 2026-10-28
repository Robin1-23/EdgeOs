from datetime import date
from decimal import Decimal
from pathlib import Path

from services.pipelines import amfi_history, amfi_nav

FIX = Path(__file__).parent / "fixtures/amfi"
HISTORY = (FIX / "NAVHistory_sample.txt").read_text(encoding="utf-8")
DAILY = (FIX / "NAVAll_sample.txt").read_text(encoding="utf-8")
TODAY = date(2026, 10, 28)


def test_parse_history_format():
    p = amfi_nav.parse(HISTORY, TODAY)
    assert len(p.schemes) == 2 and len(p.navs) == 10 and p.skipped == {"bad_nav": 1}
    lc = next(s for s in p.schemes if s[0] == "119528")
    # columns come from different positions than the daily file; section header has spaces inside the parens
    assert lc == ("119528", "Aditya Birla Sun Life Large Cap Fund - Growth - Direct Plan", "Aditya Birla Sun Life Mutual Fund",
                  "Equity Scheme - Large Cap Fund", "open", "direct", "GROWTH", "INF209K01YY7", None)
    assert ("119528", date(2026, 10, 27), Decimal("546.48")) in p.navs  # same value as the daily file that day


def test_history_never_overwrites_current_scheme_master(db_conn, monkeypatch):
    monkeypatch.setattr(amfi_history, "MIN_SCHEMES", 0)
    amfi_nav.load(db_conn, amfi_nav.parse(DAILY, TODAY), min_schemes=0)
    name_before = db_conn.execute("SELECT name FROM fund_schemes WHERE amfi_code = '119528'").fetchone()[0]

    result = amfi_history.run_month(db_conn, 2026, 10, TODAY, fetcher=lambda *_: HISTORY)
    assert (result["schemes_inserted"], result["schemes_updated"]) == (1, 0)  # only 148921 is new
    assert result["navs_inserted"] == 9  # 119528's 27-Oct NAV was already there from the daily file
    assert db_conn.execute("SELECT name FROM fund_schemes WHERE amfi_code = '119528'").fetchone()[0] == name_before

    again = amfi_history.run_month(db_conn, 2026, 10, TODAY, fetcher=lambda *_: HISTORY)
    assert (again["navs_inserted"], again["navs_updated"]) == (0, 0)


def test_months_and_ranges():
    assert amfi_history.months("2025-11", "2026-02") == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]
    assert amfi_history.month_range(2024, 2) == (date(2024, 2, 1), date(2024, 2, 29))
    assert "frmdt=01-Feb-2024&todt=29-Feb-2024" in amfi_history.URL.format(start=amfi_history.amfi_date(date(2024, 2, 1)), end=amfi_history.amfi_date(date(2024, 2, 29)))
