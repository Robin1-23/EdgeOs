"""NSE-format equity bhavcopy parser + loader: EOD rows -> securities + prices_daily. Idempotent.

Rows come from a source adapter (services/pipelines/sources.py); this module never downloads.
Usage:
  uv run python -m services.pipelines.nse_bhavcopy --date 2026-10-27 [--source dev_local]
  uv run python -m services.pipelines.nse_bhavcopy --from 2016-10-01 --to 2026-10-27   # backfill
"""

import csv
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import psycopg

from services.pipelines.common import PipelineError, backfill_days, day_cli

SERIES_PRIORITY = {"EQ": 0, "BE": 1, "BZ": 2, "SM": 3, "ST": 4}  # equity series; others (bonds, G-secs) dropped
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
MIN_ROWS = 1000  # 2016 files have ~1.6k equity rows, 2026 ~3.4k; fewer means a broken file

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
    """Normalised EOD rows for one day, the shape every source adapter returns: rows are
    (isin, symbol, series, name | None, open, high, low, close, volume, turnover | None), already validated."""
    day: date
    rows: list[tuple] = field(default_factory=list)  # COLS order
    skipped: Counter = field(default_factory=Counter)  # reason -> rows


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


def load(conn: psycopg.Connection, parsed: Parsed, source: str = "dev_local", min_rows: int | None = None) -> dict:
    """Upsert one day in one transaction; rows are written only when a value actually changed.
    source (a data_sources.name) is stamped on every row; a newer source's rows replace an older one's."""
    min_rows = MIN_ROWS if min_rows is None else min_rows
    if len(parsed.rows) < min_rows:
        raise PipelineError(f"{parsed.day}: only {len(parsed.rows)} valid rows (< {min_rows}); refusing to load a partial file")

    p = {"day": parsed.day, "source": source}
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
            """INSERT INTO securities (isin, nse_symbol, name, last_seen, source)
               SELECT b.isin,
                      CASE WHEN EXISTS (SELECT 1 FROM securities s WHERE s.nse_symbol = b.symbol AND s.isin <> b.isin)
                           THEN NULL ELSE b.symbol END,
                      b.name, %(day)s, %(source)s
               FROM _bhav b
               ON CONFLICT (isin) DO UPDATE SET
                   nse_symbol = EXCLUDED.nse_symbol,
                   name = COALESCE(EXCLUDED.name, securities.name),
                   last_seen = EXCLUDED.last_seen,
                   source = EXCLUDED.source
               WHERE (securities.last_seen IS NULL OR EXCLUDED.last_seen >= securities.last_seen)
                 AND (securities.nse_symbol, securities.name, securities.last_seen, securities.source)
                     IS DISTINCT FROM (EXCLUDED.nse_symbol, COALESCE(EXCLUDED.name, securities.name), EXCLUDED.last_seen,
                                       EXCLUDED.source)
               RETURNING xmax = 0""",
            p,
        )
        securities = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute(
            """INSERT INTO prices_daily (security_id, exchange, trade_date, open, high, low, close, volume, turnover, symbol, series,
                                         source)
               SELECT s.id, 'NSE', %(day)s, b.open, b.high, b.low, b.close, b.volume, b.turnover, b.symbol, b.series,
                      %(source)s
               FROM _bhav b JOIN securities s USING (isin)
               ON CONFLICT (security_id, exchange, trade_date) DO UPDATE SET
                   open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
                   volume = EXCLUDED.volume, turnover = EXCLUDED.turnover, symbol = EXCLUDED.symbol, series = EXCLUDED.series,
                   source = EXCLUDED.source
               WHERE (prices_daily.open, prices_daily.high, prices_daily.low, prices_daily.close, prices_daily.volume,
                      prices_daily.turnover, prices_daily.symbol, prices_daily.series, prices_daily.source)
                     IS DISTINCT FROM (EXCLUDED.open, EXCLUDED.high, EXCLUDED.low, EXCLUDED.close, EXCLUDED.volume,
                                       EXCLUDED.turnover, EXCLUDED.symbol, EXCLUDED.series, EXCLUDED.source)
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


def run_day(conn: psycopg.Connection, day: date, adapter) -> dict:
    """Load one trading day from a source adapter (see services/pipelines/sources.py)."""
    if day.weekday() >= 5:
        return {"day": day, "status": "weekend"}
    parsed = adapter.eod_prices(day)
    if parsed is None:
        return {"day": day, "status": "no_file"}  # holiday, or not published yet
    return {"day": day, "status": "loaded", **load(conn, parsed, source=adapter.name)}


def backfill(conn: psycopg.Connection, start: date, end: date, adapter) -> list[dict]:
    """Oldest first so the latest symbol/name wins."""
    return backfill_days(start, end, lambda d: run_day(conn, d, adapter))


if __name__ == "__main__":
    day_cli(__doc__, run_day)
