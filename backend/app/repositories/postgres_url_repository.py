"""
PostgreSQL-backed URL repository.

This satisfies the exact same method signatures as
InMemoryUrlRepository (exists, insert, get, delete) -- that's the
payoff of the Repository Pattern decision from Phase 1: url_service.py
needs ZERO changes to run against a real database instead of a dict.

CONCURRENCY: unlike the in-memory version, insert() here does NOT do
a separate exists() check before inserting. It relies entirely on the
database's UNIQUE constraint (see migrations/001_create_urls_table.sql)
and catches psycopg.errors.UniqueViolation on conflict. This is the
actual fix for the TOCTOU race condition discussed in the Phase 1/2
checkpoint: the uniqueness check and the write are now a single
atomic operation performed by Postgres, not two separate round-trips
from our application.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psycopg
from psycopg.rows import class_row
from psycopg_pool import ConnectionPool

from app.repositories.exceptions import ShortCodeCollisionError

__all__ = ["ShortCodeCollisionError", "UrlRecord", "PostgresUrlRepository"]


@dataclass
class UrlRecord:
    id: int
    short_code: str
    long_url: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    is_active: bool = True
    owner_id: Optional[int] = None


class PostgresUrlRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def insert(
        self,
        short_code: str,
        long_url: str,
        expires_at: Optional[datetime] = None,
        owner_id: Optional[int] = None,
    ) -> UrlRecord:
        with self._pool.connection() as conn:
            try:
                with conn.cursor(row_factory=class_row(UrlRecord)) as cur:
                    cur.execute(
                        """
                        INSERT INTO urls (short_code, long_url, expires_at, owner_id)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, short_code, long_url, created_at, expires_at, is_active, owner_id
                        """,
                        (short_code, long_url, expires_at, owner_id),
                    )
                    return cur.fetchone()
            except psycopg.errors.UniqueViolation:
                # The constraint did its job -- surface it as the same
                # exception type the service layer already knows how
                # to catch and retry on (see url_service.py).
                raise ShortCodeCollisionError(
                    f"Short code '{short_code}' already exists"
                )

    def exists(self, short_code: str) -> bool:
        """
        Retained for API-metadata lookups and for the custom-alias
        "is this taken" pre-check, where a slightly stale answer
        followed by a race-safe insert (which will still correctly
        reject on conflict) is an acceptable, honest trade-off -- see
        url_service.py's docstring on why custom alias handling
        doesn't retry blindly.
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM urls WHERE short_code = %s AND is_active", (short_code,))
                return cur.fetchone() is not None

    def get(self, short_code: str) -> Optional[UrlRecord]:
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=class_row(UrlRecord)) as cur:
                cur.execute(
                    """
                    SELECT id, short_code, long_url, created_at, expires_at, is_active, owner_id
                    FROM urls
                    WHERE short_code = %s AND is_active
                    """,
                    (short_code,),
                )
                return cur.fetchone()

    def delete(self, short_code: str) -> bool:
        """Soft delete -- see is_active column rationale in the migration."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE urls SET is_active = false WHERE short_code = %s AND is_active",
                    (short_code,),
                )
                return cur.rowcount > 0
