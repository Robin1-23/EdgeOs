from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from services.pipelines.amfi_nav import HEADER, PipelineError, load, parse

SAMPLE = (Path(__file__).parent / "fixtures/amfi/NAVAll_sample.txt").read_text(encoding="utf-8")
TODAY = date(2026, 10, 27)


def schemes_by_code(parsed):
    return {s[0]: s for s in parsed.schemes}


def test_parse_known_rows():
    p = parse(SAMPLE, TODAY)
    s = schemes_by_code(p)
    assert len(p.schemes) == len(p.navs) == 11

    assert s["135762"] == (
        "135762", "Axis Children's Fund", "Axis Mutual Fund", "Children’s Fund - Childrens' Fund",
        "open", "direct", "Growth Option", "INF846K01WO1", None,
    )
    assert ("135762", date(2026, 10, 27), Decimal("28.9301")) in p.navs
    assert s["103173"][5:] == ("regular", "IDCW", "INF209K01BO6", "INF209K01EC5")
    assert s["119527"][5:7] == (None, None)  # blank plan stays NULL, never guessed
    assert s["146839"][2:5] == ("Kotak Mahindra Mutual Fund", "Income", "interval")
    assert s["146839"][7] is None  # '-' ISIN
    assert ("146839", date(2019, 4, 17), Decimal("10.0087785")) in p.navs  # stale NAV keeps its own date
    assert s["141950"][3:5] == ("ELSS", "close")


def test_parse_skips_bad_rows():
    p = parse(SAMPLE, TODAY)
    assert p.skipped == {"bad_nav": 1, "zero_nav": 1, "bad_date": 1, "field_count": 1, "bad_code": 1, "future_date": 1}
    assert not any(code.startswith("9000") for code, *_ in p.schemes)


def test_parse_rejects_wrong_header():
    with pytest.raises(PipelineError, match="header"):
        parse("<html>Service Unavailable</html>")
    with pytest.raises(PipelineError, match="empty"):
        parse("")


def test_parse_row_outside_section():
    p = parse(f"{HEADER}\n100001;-;-;Orphan;;;10;27-Oct-2026\n", TODAY)
    assert p.schemes == [] and p.skipped == {"no_section": 1}


def test_parse_section_without_amc_line_keeps_previous_amc():
    # Real file, line ~5958: ICICI rows continue under a new section with no AMC line.
    text = f"{HEADER}\nOpen Ended Schemes(A)\nICICI Prudential Mutual Fund\nOpen Ended Schemes(Growth)\n103028;-;-;X;;;13.5898;22-Apr-2016\n"
    (scheme,) = parse(text, TODAY).schemes
    assert scheme[2:4] == ("ICICI Prudential Mutual Fund", "Growth")


def test_parse_duplicate_nav_keeps_first():
    text = f"{HEADER}\nOpen Ended Schemes(X)\nAMC\n100001;-;-;A;;;10;27-Oct-2026\n100001;-;-;A;;;11;27-Oct-2026\n"
    p = parse(text, TODAY)
    assert p.navs == [("100001", date(2026, 10, 27), Decimal("10"))] and p.skipped == {"duplicate_nav": 1}


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_load_is_idempotent(db_conn):
    p = parse(SAMPLE, TODAY)
    first = load(db_conn, p, min_schemes=0)
    assert first["schemes_inserted"] == first["navs_inserted"] == 11
    assert first["schemes_updated"] == first["navs_updated"] == 0

    second = load(db_conn, p, min_schemes=0)
    assert {k: v for k, v in second.items() if k != "skipped"} == dict.fromkeys(
        ["schemes_inserted", "schemes_updated", "navs_inserted", "navs_updated"], 0
    )
    assert count(db_conn, "fund_schemes") == count(db_conn, "fund_nav") == 11
    assert db_conn.execute("SELECT nav FROM fund_nav WHERE amfi_code = '146839'").fetchone()[0] == Decimal("10.0087785")


def test_load_picks_up_corrected_nav(db_conn):
    load(db_conn, parse(SAMPLE, TODAY), min_schemes=0)
    corrected = parse(SAMPLE.replace(";28.9301;", ";28.9400;"), TODAY)
    result = load(db_conn, corrected, min_schemes=0)
    assert (result["navs_inserted"], result["navs_updated"], result["schemes_updated"]) == (0, 1, 0)


def test_load_refuses_partial_file(db_conn):
    with pytest.raises(PipelineError, match="partial"):
        load(db_conn, parse(SAMPLE, TODAY))  # 11 schemes < MIN_SCHEMES
    assert count(db_conn, "fund_schemes") == 0
