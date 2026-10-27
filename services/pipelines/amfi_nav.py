"""AMFI daily NAV pipeline: NAVAll.txt -> fund_schemes + fund_nav. Idempotent.

Usage: uv run python -m services.pipelines.amfi_nav [--file PATH]
"""

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

from db import DATABASE_URL
from services.pipelines.common import PipelineError, http_get

URL = "https://www.amfiindia.com/spages/NAVAll.txt"
HEADER = "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date"
SECTION = re.compile(r"^(Open Ended|Close Ended|Interval Fund) Schemes\((.+)\)$")
SCHEME_TYPE = {"Open Ended": "open", "Close Ended": "close", "Interval Fund": "interval"}
PLAN = {"Direct Plan": "direct", "Regular Plan": "regular"}
MIN_SCHEMES = 5000  # full file has ~14k; fewer means a truncated or broken download

SCHEME_COLS = "amfi_code, name, amc, category, scheme_type, plan, option, isin_growth, isin_reinvest"


@dataclass
class Parsed:
    schemes: list[tuple] = field(default_factory=list)  # SCHEME_COLS order
    navs: list[tuple] = field(default_factory=list)  # (amfi_code, nav_date, nav)
    skipped: Counter = field(default_factory=Counter)  # reason -> rows


def fetch(url: str = URL) -> str:
    body = http_get(url)
    if body is None:
        raise PipelineError(f"{url} returned 404")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PipelineError(f"file is not UTF-8: {e}") from e


def _parse_row(line: str, today: date) -> tuple | str:
    """Return (code, isin_growth, isin_reinvest, name, plan, option, nav, nav_date) or a skip reason."""
    f = [x.strip() for x in line.split(";")]
    if len(f) != 8:
        return "field_count"
    code, isin_g, isin_r, name, plan, option, nav_s, date_s = f
    if not code.isdigit():
        return "bad_code"
    if plan and plan not in PLAN:
        return "unknown_plan"
    try:
        nav = Decimal(nav_s)
    except InvalidOperation:
        return "bad_nav"
    if nav == 0:
        return "zero_nav"  # e.g. written-off segregated portfolios; fund_nav requires nav > 0
    if not nav.is_finite() or nav < 0:
        return "bad_nav"
    try:
        nav_date = datetime.strptime(date_s, "%d-%b-%Y").date()
    except ValueError:
        return "bad_date"
    if nav_date > today:
        return "future_date"
    isin_g, isin_r = (None if s in ("", "-") else s for s in (isin_g, isin_r))
    return code, isin_g, isin_r, name, PLAN.get(plan), option or None, nav, nav_date


def parse(text: str, today: date | None = None) -> Parsed:
    today = today or date.today()
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != HEADER:
        raise PipelineError(f"unexpected header: {(lines[0][:120] if lines else '<empty file>')!r}")

    out, seen = Parsed(), set()
    scheme_type = category = amc = None
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        if ";" not in line:
            if m := SECTION.match(line):
                # AMC carries over: the real file sometimes starts a section with no AMC line.
                scheme_type, category = SCHEME_TYPE[m[1]], m[2]
            else:
                amc = line
            continue

        row = _parse_row(line, today)
        if isinstance(row, str):
            out.skipped[row] += 1
            continue
        code, isin_g, isin_r, name, plan, option, nav, nav_date = row
        if category is None or amc is None:
            out.skipped["no_section"] += 1
        elif code in seen:
            out.skipped["duplicate_code"] += 1
        else:
            seen.add(code)
            out.schemes.append((code, name, amc, category, scheme_type, plan, option, isin_g, isin_r))
            out.navs.append((code, nav_date, nav))
    return out


def load(conn: psycopg.Connection, parsed: Parsed, min_schemes: int = MIN_SCHEMES) -> dict:
    """Upsert in one transaction; rows are written only when a value actually changed."""
    if len(parsed.schemes) < min_schemes:
        raise PipelineError(f"only {len(parsed.schemes)} valid schemes (< {min_schemes}); refusing to load a partial file")

    set_cols = [c.strip() for c in SCHEME_COLS.split(",")][1:]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _schemes (LIKE fund_schemes); CREATE TEMP TABLE _navs (LIKE fund_nav)")
        with cur.copy(f"COPY _schemes ({SCHEME_COLS}) FROM STDIN") as cp:
            for r in parsed.schemes:
                cp.write_row(r)
        with cur.copy("COPY _navs (amfi_code, nav_date, nav) FROM STDIN") as cp:
            for r in parsed.navs:
                cp.write_row(r)

        cur.execute(
            f"""INSERT INTO fund_schemes ({SCHEME_COLS}) SELECT {SCHEME_COLS} FROM _schemes
                ON CONFLICT (amfi_code) DO UPDATE SET {", ".join(f"{c} = EXCLUDED.{c}" for c in set_cols)}
                WHERE ({", ".join(f"fund_schemes.{c}" for c in set_cols)})
                      IS DISTINCT FROM ({", ".join(f"EXCLUDED.{c}" for c in set_cols)})
                RETURNING xmax = 0"""
        )
        schemes = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute(
            """INSERT INTO fund_nav (amfi_code, nav_date, nav) SELECT amfi_code, nav_date, nav FROM _navs
               ON CONFLICT (amfi_code, nav_date) DO UPDATE SET nav = EXCLUDED.nav
               WHERE fund_nav.nav IS DISTINCT FROM EXCLUDED.nav
               RETURNING xmax = 0"""
        )
        navs = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute("DROP TABLE _schemes, _navs")

    return {
        "schemes_inserted": schemes[True],
        "schemes_updated": schemes[False],
        "navs_inserted": navs[True],
        "navs_updated": navs[False],
        "skipped": dict(parsed.skipped),
    }


def run(conn: psycopg.Connection, file: Path | None = None) -> dict:
    text = file.read_text(encoding="utf-8") if file else fetch()
    return load(conn, parse(text))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", type=Path, help="load a saved NAVAll.txt instead of downloading")
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL) as conn:
        print(run(conn, args.file))
