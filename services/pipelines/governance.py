"""Shareholding pattern and corporate events (auditor changes) -> shareholding, corporate_events,
corporate_events_coverage. Idempotent.

Rows come from a source adapter (services/pipelines/sources.py); this module never downloads.
Usage: uv run python -m services.pipelines.governance --isin INE002A01018 [--source licensed_vendor]
"""

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

import psycopg

from db import DATABASE_URL

ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
EVENT_KINDS = {"auditor_resignation", "auditor_appointment"}


@dataclass
class Parsed:
    """shareholding rows: (isin, quarter_end, promoter_pct, promoter_pledged_pct) with pledged as % of promoter
    shares; event rows: (isin, event_date, kind, detail). For events, covered_until is the date up to which the
    source checked the company (so an empty list means 'none found', not 'unknown')."""
    rows: list[tuple] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    covered_until: date | None = None


def _pct(v) -> Decimal | None:
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() and 0 <= d <= 100 else None


def build_shareholding(records) -> Parsed:
    out, seen = Parsed(), set()
    for isin, quarter_end, promoter, pledged in records:
        p, q = _pct(promoter), _pct(pledged)
        if not (isinstance(isin, str) and ISIN.match(isin)) or not isinstance(quarter_end, date):
            reason = "bad_key"
        elif p is None or q is None:
            reason = "bad_pct"
        elif (isin, quarter_end) in seen:
            reason = "duplicate"
        else:
            reason = None
        if reason:
            out.skipped[reason] += 1
            continue
        seen.add((isin, quarter_end))
        out.rows.append((isin, quarter_end, p, q))
    return out


def build_events(records, covered_until: date | None) -> Parsed:
    out, seen = Parsed(covered_until=covered_until), set()
    for isin, event_date, kind, detail in records:
        detail = (detail or "").strip()
        if not (isinstance(isin, str) and ISIN.match(isin)) or not isinstance(event_date, date):
            reason = "bad_key"
        elif kind not in EVENT_KINDS:
            reason = "unknown_kind"
        elif (isin, event_date, kind, detail) in seen:
            reason = "duplicate"
        else:
            reason = None
        if reason:
            out.skipped[reason] += 1
            continue
        seen.add((isin, event_date, kind, detail))
        out.rows.append((isin, event_date, kind, detail))
    return out


def _sid(conn, isin: str) -> int | None:
    row = conn.execute("SELECT id FROM securities WHERE isin = %s", (isin,)).fetchone()
    return row[0] if row else None


def load_shareholding(conn: psycopg.Connection, parsed: Parsed, source: str = "dev_local") -> dict:
    written, skipped = Counter(), Counter(parsed.skipped)
    with conn.transaction():
        for isin, qe, p, q in parsed.rows:
            if (sid := _sid(conn, isin)) is None:
                skipped["unknown_security"] += 1
                continue
            row = conn.execute(
                """INSERT INTO shareholding (security_id, quarter_end, promoter_pct, promoter_pledged_pct, source)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (security_id, quarter_end) DO UPDATE SET promoter_pct = EXCLUDED.promoter_pct,
                       promoter_pledged_pct = EXCLUDED.promoter_pledged_pct, source = EXCLUDED.source
                   WHERE (shareholding.promoter_pct, shareholding.promoter_pledged_pct, shareholding.source)
                         IS DISTINCT FROM (EXCLUDED.promoter_pct, EXCLUDED.promoter_pledged_pct, EXCLUDED.source)
                   RETURNING xmax = 0""", (sid, qe, p, q, source)).fetchone()
            if row:
                written[row[0]] += 1
    return {"inserted": written[True], "updated": written[False], "skipped": dict(skipped)}


def load_events(conn: psycopg.Connection, isin: str, parsed: Parsed, source: str = "dev_local") -> dict:
    """Events for one company plus its coverage date (only ever moved forward)."""
    if (sid := _sid(conn, isin)) is None:
        return {"inserted": 0, "skipped": {"unknown_security": len(parsed.rows)}}
    inserted, skipped = 0, Counter(parsed.skipped)
    with conn.transaction():
        for e_isin, d, kind, detail in parsed.rows:
            if e_isin != isin:
                skipped["other_security"] += 1
                continue
            inserted += conn.execute(
                """INSERT INTO corporate_events (security_id, event_date, kind, detail, source) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""", (sid, d, kind, detail, source)).rowcount
        if parsed.covered_until:
            conn.execute(
                """INSERT INTO corporate_events_coverage (security_id, covered_until, source) VALUES (%s, %s, %s)
                   ON CONFLICT (security_id) DO UPDATE SET covered_until = EXCLUDED.covered_until, source = EXCLUDED.source
                   WHERE EXCLUDED.covered_until > corporate_events_coverage.covered_until""", (sid, parsed.covered_until, source))
    return {"inserted": inserted, "skipped": dict(skipped)}


def run_security(conn: psycopg.Connection, isin: str, adapter) -> dict:
    out = {"isin": isin}
    sh, ev = adapter.shareholding(isin), adapter.corporate_events(isin)
    out["shareholding"] = {"status": "no_data"} if sh is None else {"status": "loaded", **load_shareholding(conn, sh, adapter.name)}
    out["events"] = {"status": "no_data"} if ev is None else {"status": "loaded", **load_events(conn, isin, ev, adapter.name)}
    return out


if __name__ == "__main__":
    from services.pipelines.sources import ADAPTERS, get_adapter

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--isin", required=True)
    ap.add_argument("--source", choices=sorted(ADAPTERS))
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL) as conn:
        print(run_security(conn, args.isin, get_adapter(args.source)))
