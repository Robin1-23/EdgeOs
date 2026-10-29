"""Apply db/migrations/*.sql in order, once each. Usage: uv run python -m db.migrate"""

from pathlib import Path

import psycopg

from db import DATABASE_URL

MIGRATIONS = sorted((Path(__file__).parent / "migrations").glob("*.sql"))


def migrate(conn: psycopg.Connection) -> list[str]:
    applied = []
    with conn.transaction():
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in MIGRATIONS:
            if path.name not in done:
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                applied.append(path.name)
    return applied


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL, connect_timeout=10) as conn:
        print("applied:", migrate(conn) or "nothing new")
