"""NSE-format daily index closes (ind_close_all CSV) parser + loader -> index_prices. Idempotent.

Rows come from a source adapter (services/pipelines/sources.py); this module never downloads.
Usage:
  uv run python -m services.pipelines.nse_indices --date 2026-10-27 [--source dev_local]
  uv run python -m services.pipelines.nse_indices --from 2016-10-01 --to 2026-10-27   # backfill
"""

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import psycopg

from services.pipelines.common import PipelineError, backfill_days, day_cli

REQUIRED_INDEX = "Nifty 50"  # a file without it is broken; M1 relative strength depends on it
MIN_ROWS = 40  # 2016 files carry ~70 indices, 2026 ~170
NUMERIC = {"open": "Open Index Value", "high": "High Index Value", "low": "Low Index Value", "close": "Closing Index Value",
           "volume": "Volume", "turnover_cr": "Turnover (Rs. Cr.)", "pe": "P/E", "pb": "P/B", "div_yield": "Div Yield"}
COLS = "index_name, open, high, low, close, volume, turnover_cr, pe, pb, div_yield"


@dataclass
class Parsed:
    """Normalised index closes for one day, the shape every source adapter returns: rows are
    (index_name, open | None, high | None, low | None, close, volume, turnover_cr, pe, pb, div_yield), validated."""
    day: date
    rows: list[tuple] = field(default_factory=list)  # COLS order
    skipped: Counter = field(default_factory=Counter)


def _parse_row(r: dict) -> tuple | str:
    name = (r.get("Index Name") or "").strip()
    if not name:
        return "no_name"
    try:
        v = {k: (None if (s := (r.get(col) or "").strip()) in ("", "-") else Decimal(s)) for k, col in NUMERIC.items()}
    except InvalidOperation:
        return "bad_number"
    o, h, lo, c = v["open"], v["high"], v["low"], v["close"]
    if c is None or not c.is_finite() or c <= 0:
        return "bad_close"
    if None in (o, h, lo):
        o = h = lo = None  # close-only series (VIX components, dividend points): keep the close
    elif not (min(o, h, lo) > 0 and lo <= min(o, c) and h >= max(o, c)):
        return "bad_ohlc"
    volume = int(v["volume"]) if v["volume"] is not None and v["volume"] == v["volume"].to_integral() else None
    return name, o, h, lo, c, volume, v["turnover_cr"], v["pe"], v["pb"], v["div_yield"]


def parse(data: bytes, day: date) -> Parsed:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise PipelineError(f"{day}: not UTF-8: {e}") from e
    reader = csv.DictReader(io.StringIO(text))
    missing = {"Index Name", "Index Date", *NUMERIC.values()} - set(reader.fieldnames or [])
    if missing:
        raise PipelineError(f"{day}: unrecognised header, missing {sorted(missing)}")

    out, seen = Parsed(day), set()
    for r in reader:
        try:
            file_day = datetime.strptime((r.get("Index Date") or "").strip(), "%d-%m-%Y").date()
        except ValueError:
            raise PipelineError(f"{day}: bad index date {r.get('Index Date')!r}") from None
        if file_day != day:
            raise PipelineError(f"file is for {file_day}, expected {day}")
        row = _parse_row(r)
        if isinstance(row, str):
            out.skipped[row] += 1
        elif row[0] in seen:
            out.skipped["duplicate_index"] += 1
        else:
            seen.add(row[0])
            out.rows.append(row)
    return out


def load(conn: psycopg.Connection, parsed: Parsed, source: str = "dev_local", min_rows: int | None = None) -> dict:
    min_rows = MIN_ROWS if min_rows is None else min_rows
    if len(parsed.rows) < min_rows:
        raise PipelineError(f"{parsed.day}: only {len(parsed.rows)} valid indices (< {min_rows}); refusing to load a partial file")
    if REQUIRED_INDEX not in {r[0] for r in parsed.rows}:
        raise PipelineError(f"{parsed.day}: {REQUIRED_INDEX} missing from file")

    value_cols = [c.strip() for c in COLS.split(",")][1:] + ["source"]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _idx (LIKE index_prices)")
        with cur.copy(f"COPY _idx ({COLS}, trade_date, source) FROM STDIN") as cp:
            for r in parsed.rows:
                cp.write_row((*r, parsed.day, source))
        cur.execute(
            f"""INSERT INTO index_prices ({COLS}, trade_date, source) SELECT {COLS}, trade_date, source FROM _idx
                ON CONFLICT (index_name, trade_date) DO UPDATE SET {", ".join(f"{c} = EXCLUDED.{c}" for c in value_cols)}
                WHERE ({", ".join(f"index_prices.{c}" for c in value_cols)})
                      IS DISTINCT FROM ({", ".join(f"EXCLUDED.{c}" for c in value_cols)})
                RETURNING xmax = 0"""
        )
        written = Counter(inserted for (inserted,) in cur.fetchall())
        cur.execute("DROP TABLE _idx")
    return {"indices_inserted": written[True], "indices_updated": written[False], "skipped": dict(parsed.skipped)}


def run_day(conn: psycopg.Connection, day: date, adapter) -> dict:
    """Load one trading day from a source adapter (see services/pipelines/sources.py)."""
    if day.weekday() >= 5:
        return {"day": day, "status": "weekend"}
    parsed = adapter.index_closes(day)
    if parsed is None:
        return {"day": day, "status": "no_file"}  # holiday, or not published yet
    return {"day": day, "status": "loaded", **load(conn, parsed, source=adapter.name)}


def backfill(conn: psycopg.Connection, start: date, end: date, adapter) -> list[dict]:
    return backfill_days(start, end, lambda d: run_day(conn, d, adapter))


if __name__ == "__main__":
    day_cli(__doc__, run_day)
