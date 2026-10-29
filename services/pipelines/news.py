"""News headlines -> news_items. Idempotent. Headline, link, publisher and time only: no article bodies.

Rows come from a source adapter's news(since) (services/pipelines/sources.py); this module never downloads.
Which company a headline is about is decided later by M5's alias matching, not here.
Usage: uv run python -m services.pipelines.news --since 2026-10-01 [--source licensed_vendor]
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

import psycopg

from db import DATABASE_URL

MAX_HEADLINE = 400


@dataclass
class Parsed:
    """rows: (published_at (aware datetime), publisher, headline, url https://...)."""
    rows: list[tuple] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)


def build(records) -> Parsed:
    out, seen = Parsed(), set()
    for published_at, publisher, headline, url in records:
        headline = " ".join(headline.split()) if isinstance(headline, str) else ""
        if not isinstance(published_at, datetime) or published_at.tzinfo is None:
            reason = "bad_published_at"
        elif not (isinstance(publisher, str) and publisher.strip()):
            reason = "no_publisher"
        elif not 0 < len(headline) <= MAX_HEADLINE:
            reason = "bad_headline"
        elif not (isinstance(url, str) and url.startswith("https://")):
            reason = "bad_url"
        elif url in seen:
            reason = "duplicate"
        else:
            reason = None
        if reason:
            out.skipped[reason] += 1
            continue
        seen.add(url)
        out.rows.append((published_at, publisher.strip(), headline, url))
    return out


def load(conn: psycopg.Connection, parsed: Parsed, source: str = "dev_local") -> dict:
    written = Counter()
    with conn.transaction():
        for published_at, publisher, headline, url in parsed.rows:
            r = conn.execute(
                """INSERT INTO news_items (published_at, publisher, headline, url, source) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET published_at = EXCLUDED.published_at, publisher = EXCLUDED.publisher,
                       headline = EXCLUDED.headline, source = EXCLUDED.source
                   WHERE (news_items.published_at, news_items.publisher, news_items.headline, news_items.source)
                         IS DISTINCT FROM (EXCLUDED.published_at, EXCLUDED.publisher, EXCLUDED.headline, EXCLUDED.source)
                   RETURNING xmax = 0""", (published_at, publisher, headline, url, source)).fetchone()
            if r:
                written[r[0]] += 1
    return {"inserted": written[True], "updated": written[False], "skipped": dict(parsed.skipped)}


def run(conn: psycopg.Connection, since: date, adapter) -> dict:
    parsed = adapter.news(since)
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
