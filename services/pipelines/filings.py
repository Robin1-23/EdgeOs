"""Exchange filings (announcements, results, concall transcripts) with their text -> filings. Idempotent.

Rows come from a source adapter's filings(since) (services/pipelines/sources.py); this module never downloads.
Usage: uv run python -m services.pipelines.filings --since 2026-07-01 [--source licensed_vendor]
"""

import argparse
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

import psycopg

from db import DATABASE_URL

ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
CATEGORIES = {"results", "concall", "announcement", "investor_presentation", "annual_report", "other"}


@dataclass
class Parsed:
    """rows: (isin, exchange 'NSE'|'BSE', filed_at (aware datetime), category in CATEGORIES, title, url, text | None).
    text is the filing's plain text (PDF extraction is the adapter's job); None when only metadata is known."""
    rows: list[tuple] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)


def build(records) -> Parsed:
    out, seen = Parsed(), set()
    for isin, exchange, filed_at, category, title, url, text in records:
        if not (isinstance(isin, str) and ISIN.match(isin)):
            reason = "bad_isin"
        elif exchange not in ("NSE", "BSE"):
            reason = "bad_exchange"
        elif not isinstance(filed_at, datetime) or filed_at.tzinfo is None:
            reason = "bad_filed_at"
        elif category not in CATEGORIES:
            reason = "unknown_category"
        elif not (isinstance(title, str) and title.strip()) or not (isinstance(url, str) and url.startswith("https://")):
            reason = "bad_title_or_url"
        elif url in seen:
            reason = "duplicate"
        else:
            reason = None
        if reason:
            out.skipped[reason] += 1
            continue
        seen.add(url)
        body = text.strip() if isinstance(text, str) and text.strip() else None
        out.rows.append((isin, exchange, filed_at, category, title.strip(), url, body))
    return out


def sha256(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def load(conn: psycopg.Connection, parsed: Parsed, source: str = "dev_local") -> dict:
    """Upsert by URL; text and its hash are updated when the source sends a corrected version."""
    written, skipped = Counter(), Counter(parsed.skipped)
    with conn.transaction():
        for isin, exchange, filed_at, category, title, url, text in parsed.rows:
            row = conn.execute("SELECT id FROM securities WHERE isin = %s", (isin,)).fetchone()
            if row is None:
                skipped["unknown_security"] += 1
                continue
            r = conn.execute(
                """INSERT INTO filings (security_id, exchange, filed_at, category, title, url, body_text, text_sha256, source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET filed_at = EXCLUDED.filed_at, category = EXCLUDED.category,
                       title = EXCLUDED.title, body_text = EXCLUDED.body_text, text_sha256 = EXCLUDED.text_sha256,
                       source = EXCLUDED.source
                   WHERE (filings.filed_at, filings.category, filings.title, filings.text_sha256, filings.source)
                         IS DISTINCT FROM (EXCLUDED.filed_at, EXCLUDED.category, EXCLUDED.title, EXCLUDED.text_sha256, EXCLUDED.source)
                   RETURNING xmax = 0""",
                (row[0], exchange, filed_at, category, title, url, text, sha256(text), source)).fetchone()
            if r:
                written[r[0]] += 1
    return {"inserted": written[True], "updated": written[False], "skipped": dict(skipped)}


def run(conn: psycopg.Connection, since: date, adapter) -> dict:
    parsed = adapter.filings(since)
    if parsed is None:
        return {"status": "no_data"}
    return {"status": "loaded", **load(conn, parsed, adapter.name)}


if __name__ == "__main__":
    from services.pipelines.sources import ADAPTERS, get_adapter

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=date.fromisoformat, required=True)
    ap.add_argument("--source", choices=sorted(ADAPTERS))
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL) as conn:
        print(run(conn, args.since, get_adapter(args.source)))
