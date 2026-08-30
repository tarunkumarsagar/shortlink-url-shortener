"""
Minimal migration runner.

WHY not Alembic (documented properly in the ADR): we want raw,
readable SQL files as the primary artifact for learning purposes, and
this project's schema evolution is linear and small enough that we
don't yet need Alembic's autogeneration/branching/downgrade machinery.
This script does the one thing that actually matters: apply each .sql
file in migrations/ exactly once, in order, tracked in a
schema_migrations table so re-running this script is always safe
(idempotent).
"""

import sys
from pathlib import Path

import psycopg

from app.core.config import settings

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


def run_migrations() -> None:
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        applied = {
            row[0]
            for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not migration_files:
            print("No migration files found.")
            return

        for path in migration_files:
            if path.name in applied:
                print(f"SKIP  {path.name} (already applied)")
                continue

            print(f"APPLY {path.name}")
            sql = path.read_text()
            try:
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)",
                        (path.name,),
                    )
            except Exception as e:
                print(f"FAILED applying {path.name}: {e}", file=sys.stderr)
                raise

        print("Migrations up to date.")


if __name__ == "__main__":
    run_migrations()
