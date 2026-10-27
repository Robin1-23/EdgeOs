"""NSE equity bhavcopy pipeline: daily EOD file -> securities + prices_daily. Idempotent.

Usage:
  uv run python -m services.pipelines.nse_bhavcopy --date 2026-10-27
  uv run python -m services.pipelines.nse_bhavcopy --from 2016-01-01 --to 2026-10-27   # backfill
  uv run python -m services.pipelines.nse_bhavcopy --date 2026-10-27 --file PATH.zip
"""

import argparse
import csv
import io
import os
import re
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

from db import DATABASE_URL
from services.pipelines.common import PipelineError, http_get

UDIFF_FROM = date(2024, 7, 8)  # first day NSE serves only the UDiFF format
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
SERIES_PRIORITY = {"EQ": 0, "BE": 1, "BZ": 2, "SM": 3, "ST": 4}  # equity series; others (bonds, G-secs) dropped
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
MIN_ROWS = 1000  # 2016 files have ~1.6k equity rows, 2026 ~3.4k; fewer means a broken file
RAW_DIR = Path(os.environ.get("RAW_DATA_DIR", "data/raw")) / "nse"
POLITE_PAUSE = 1.0  # seconds after each real download

# (date column, date format, column map); name is absent in the pre-2024 format.
FORMATS = {
    "TckrSymb": ("TradDt", "%Y-%m-%d", dict(isin="ISIN", symbol="TckrSymb", series="SctySrs", name="FinInstrmNm",
                 open="OpnPric", high="HghPric", low="LwPric", close="ClsPric", volume="TtlTradgVol", turnover="TtlTrfVal")),
    "SYMBOL": ("TIMESTAMP", "%d-%b-%Y", dict(isin="ISIN", symbol="SYMBOL", series="SERIES", name=None,
               open="OPEN", high="HIGH", low="LOW", close="CLOSE", volume="TOTTRDQTY", turnover="TOTTRDVAL")),
}
COLS = "isin, symbol, series, name, open, high, low, close, volume, turnover"


@dataclass
class Parsed:
    day: date
    rows: list[tuple] = field(default_factory=list)  # COLS order
    skipped: Counter = field(default_factory=Counter)  # reason -> rows


def url_for(day: date) -> str:
    if day >= UDIFF_FROM:
        return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    mon = MONTHS[day.month - 1]
    return f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{day:%Y}/{mon}/cm{day:%d}{mon}{day:%Y}bhav.csv.zip"


def fetch(day: date) -> bytes | None:
    """Zip bytes for the day, from the local cache when present; None if NSE has no file (holiday)."""
    url = url_for(day)
    cached = RAW_DIR / url.rsplit("/", 1)[1]
    if cached.exists():
        return cached.read_bytes()
    data = http_get(url)
    time.sleep(POLITE_PAUSE)
    if data is not None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return data


def _parse_row(r: dict, cols: dict) -> tuple | str:
    def get(k: str) -> str:
        return (r.get(cols[k]) or "").strip() if cols[k] else ""

    isin, symbol, series = get("isin"), get("symbol"), get("series")
    if series not in SERIES_PRIORITY:
        return "non_equity_series"
    if not ISIN.match(isin) or not symbol:
        return "bad_isin_or_symbol"
    try:
        o, h, lo, c = (Decimal(get(k)) for k in ("open", "high", "low", "close"))
        turnover = Decimal(get("turnover")) if get("turnover") else None
    except InvalidOperation:
        return "bad_number"
    if not all(p.is_finite() and p > 0 for p in (o, h, lo, c)):
        return "non_positive_price"
    if not (lo <= min(o, c) and h >= max(o, c)):
        return "bad_ohlc"
    if not get("volume").isdigit():
        return "bad_volume"
    return isin, symbol, series, get("name") or None, o, h, lo, c, int(get("volume")), turnover


def parse(data: bytes, day: date) -> Parsed:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            (inner,) = z.namelist()
            text = z.read(inner).decode("utf-8")
    except (zipfile.BadZipFile, ValueError, UnicodeDecodeError) as e:
        raise PipelineError(f"{day}: not a single-CSV zip: {e}") from e

    reader = csv.DictReader(io.StringIO(text))
    fmt = next((FORMATS[k] for k in FORMATS if k in (reader.fieldnames or [])), None)
    if fmt is None:
        raise PipelineError(f"{day}: unrecognised header {reader.fieldnames!r:.120}")
    date_col, date_fmt, cols = fmt

    out = Parsed(day)
    by_isin: dict[str, tuple] = {}
    for r in reader:
        try:
            file_day = datetime.strptime((r.get(date_col) or "").strip(), date_fmt).date()
        except ValueError:
            raise PipelineError(f"{day}: bad trade date {r.get(date_col)!r}") from None
        if file_day != day:
            raise PipelineError(f"file is for {file_day}, expected {day}")
        row = _parse_row(r, cols)
        if isinstance(row, str):
            out.skipped[row] += 1
            continue
        prev = by_isin.get(row[0])
        if prev is not None:
            out.skipped["duplicate_isin"] += 1
            if SERIES_PRIORITY[prev[2]] <= SERIES_PRIORITY[row[2]]:
                continue
        by_isin[row[0]] = row

    by_symbol: dict[str, tuple] = {}
    for row in by_isin.values():
        prev = by_symbol.get(row[1])
        if prev is not None:
            out.skipped["duplicate_symbol"] += 1
            if SERIES_PRIORITY[prev[2]] <= SERIES_PRIORITY[row[2]]:
                continue
        by_symbol[row[1]] = row
    out.rows = list(by_symbol.values())
    return out


def load(conn: psycopg.Connection, parsed: Parsed, min_rows: int | None = None) -> dict:
    """Upsert one day in one transaction; rows are written only when a value actually changed."""
    min_rows = MIN_ROWS if min_rows is None else min_rows
    if len(parsed.rows) < min_rows:
        raise PipelineError(f"{parsed.day}: only {len(parsed.rows)} valid rows (< {min_rows}); refusing to load a partial file")

    p = {"day": parsed.day}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE _bhav (isin text, symbol text, series text, name text, open numeric, high numeric,"
            " low numeric, close numeric, volume bigint, turnover numeric)"
        )
        with cur.copy(f"COPY _bhav ({COLS}) FROM STDIN") as cp:
            for r in parsed.rows:
                cp.write_row(r)

        # A symbol that moved to a new ISIN is freed from its old holder, but only by a newer file.
        cur.execute(
            """UPDATE securities s SET nse_symbol = NULL FROM _bhav b
               WHERE s.nse_symbol = b.symbol AND s.isin <> b.isin AND s.last_seen < %(day)s""",
            p,
        )
        cur.execute(
            """INSERT INTO securities (isin, nse_symbol, name, last_seen)
               SELECT b.isin,
                      CASE WHEN EXISTS (SELECT 1 FROM securities s WHERE s.nse_symbol = b.symbol AND s.isin <> b.isin)
                           THEN NULL ELSE b.symbol END,
                      b.name, %(day)s
               FROM _bhav b
               ON CONFLICT (isin) DO UPDATE SET
                   nse_symbol = EXCLUDED.nse_symbol,
                   name = COALESCE(EXCLUDED.name, securities.name),
                   last_seen = EXCLUDED.last_seen
               WHERE (securities.last_seen IS NULL OR EXCLUDED.last_seen >= securities.last_seen)
                 AND (securities.nse_symbol, securities.name, securities.last_seen)
                     IS DISTINCT FROM (EXCLUDED.nse_symbol, COALESCE(EXCLUDED.name, securities.name), EXCLUDED.last_seen)
               RETURNING xmax = 0""",
            p,
        )
        securities = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute(
            """INSERT INTO prices_daily (security_id, exchange, trade_date, open, high, low, close, volume, turnover, symbol, series)
               SELECT s.id, 'NSE', %(day)s, b.open, b.high, b.low, b.close, b.volume, b.turnover, b.symbol, b.series
               FROM _bhav b JOIN securities s USING (isin)
               ON CONFLICT (security_id, exchange, trade_date) DO UPDATE SET
                   open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
                   volume = EXCLUDED.volume, turnover = EXCLUDED.turnover, symbol = EXCLUDED.symbol, series = EXCLUDED.series
               WHERE (prices_daily.open, prices_daily.high, prices_daily.low, prices_daily.close, prices_daily.volume,
                      prices_daily.turnover, prices_daily.symbol, prices_daily.series)
                     IS DISTINCT FROM (EXCLUDED.open, EXCLUDED.high, EXCLUDED.low, EXCLUDED.close, EXCLUDED.volume,
                                       EXCLUDED.turnover, EXCLUDED.symbol, EXCLUDED.series)
               RETURNING xmax = 0""",
            p,
        )
        prices = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute("DROP TABLE _bhav")

    return {
        "securities_inserted": securities[True],
        "securities_updated": securities[False],
        "prices_inserted": prices[True],
        "prices_updated": prices[False],
        "skipped": dict(parsed.skipped),
    }


def run_day(conn: psycopg.Connection, day: date, fetcher: Callable[[date], bytes | None] = fetch) -> dict:
    if day.weekday() >= 5:
        return {"day": day, "status": "weekend"}
    data = fetcher(day)
    if data is None:
        return {"day": day, "status": "no_file"}  # holiday, or not published yet
    return {"day": day, "status": "loaded", **load(conn, parse(data, day))}


def backfill(conn: psycopg.Connection, start: date, end: date, fetcher: Callable[[date], bytes | None] = fetch) -> list[dict]:
    """Oldest first so the latest symbol/name wins; a failed day is reported, not fatal."""
    results = []
    for n in range((end - start).days + 1):
        day = start + timedelta(days=n)
        try:
            results.append(run_day(conn, day, fetcher))
        except PipelineError as e:
            results.append({"day": day, "status": "failed", "error": str(e)})
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, help="single trading day")
    ap.add_argument("--from", dest="start", type=date.fromisoformat, help="backfill start (inclusive)")
    ap.add_argument("--to", dest="end", type=date.fromisoformat, help="backfill end (inclusive)")
    ap.add_argument("--file", type=Path, help="load a saved zip for --date instead of downloading")
    args = ap.parse_args()
    if bool(args.start) != bool(args.end) or bool(args.date) == bool(args.start):
        ap.error("give --date, or both --from and --to")

    with psycopg.connect(DATABASE_URL) as conn:
        if args.date:
            fetcher = (lambda _: args.file.read_bytes()) if args.file else fetch
            results = backfill(conn, args.date, args.date, fetcher)
        else:
            results = backfill(conn, args.start, args.end)
        for r in results:
            print(r)
    sys.exit(1 if any(r["status"] == "failed" for r in results) else 0)
