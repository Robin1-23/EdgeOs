"""Sector / industry classification -> securities.sector, securities.industry (+ classification_source). Idempotent.

Rows come from a source adapter's classification() (services/pipelines/sources.py); this module never downloads.
Usage: uv run python -m services.pipelines.classification [--source licensed_vendor]
"""

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field

import psycopg

from db import DATABASE_URL

ISIN = re.compile(r"^IN[A-Z0-9]{10}$")


@dataclass
class Parsed:
    """Normalised classification, the shape every source adapter returns: rows are (isin, sector, industry),
    one per ISIN, non-empty strings."""
    rows: list[tuple] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)


def build(records) -> Parsed:
    out, seen = Parsed(), set()
    for isin, sector, industry in records:
        sector, industry = (s.strip() if isinstance(s, str) else "" for s in (sector, industry))
        if not isinstance(isin, str) or not ISIN.match(isin):
            out.skipped["bad_isin"] += 1
        elif not sector or not industry:
            out.skipped["missing_sector_or_industry"] += 1
        elif isin in seen:
            out.skipped["duplicate"] += 1
        else:
            seen.add(isin)
            out.rows.append((isin, sector, industry))
    return out


def load(conn: psycopg.Connection, parsed: Parsed, source: str = "dev_local") -> dict:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _cls (isin text, sector text, industry text)")
        with cur.copy("COPY _cls FROM STDIN") as cp:
            for r in parsed.rows:
                cp.write_row(r)
        (unknown,) = cur.execute("SELECT count(*) FROM _cls c WHERE NOT EXISTS (SELECT 1 FROM securities s WHERE s.isin = c.isin)").fetchone()
        cur.execute(
            """UPDATE securities s SET sector = c.sector, industry = c.industry, classification_source = %s
               FROM _cls c WHERE s.isin = c.isin
                 AND (s.sector, s.industry, s.classification_source) IS DISTINCT FROM (c.sector, c.industry, %s)""",
            (source, source),
        )
        updated = cur.rowcount
        cur.execute("DROP TABLE _cls")
    skipped = dict(parsed.skipped)
    if unknown:
        skipped["unknown_security"] = unknown
    return {"securities_updated": updated, "skipped": skipped}


def run(conn: psycopg.Connection, adapter) -> dict:
    parsed = adapter.classification()
    if parsed is None:
        return {"status": "no_data"}
    return {"status": "loaded", **load(conn, parsed, source=adapter.name)}


if __name__ == "__main__":
    from services.pipelines.sources import ADAPTERS, get_adapter

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=sorted(ADAPTERS))
    args = ap.parse_args()
    with psycopg.connect(DATABASE_URL) as conn:
        print(run(conn, get_adapter(args.source)))
