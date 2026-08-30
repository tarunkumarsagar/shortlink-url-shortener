"""
Users repository. Same layering pattern as the url repositories:
thin, storage-only, no business logic (that lives in auth_service.py).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psycopg
from psycopg.rows import class_row
from psycopg_pool import ConnectionPool

from app.repositories.exceptions import EmailAlreadyRegisteredError


@dataclass
class UserRecord:
    id: int
    email: str
    password_hash: str
    created_at: datetime
    is_active: bool


class PostgresUserRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def insert(self, email: str, password_hash: str) -> UserRecord:
        with self._pool.connection() as conn:
            try:
                with conn.cursor(row_factory=class_row(UserRecord)) as cur:
                    cur.execute(
                        """
                        INSERT INTO users (email, password_hash)
                        VALUES (%s, %s)
                        RETURNING id, email, password_hash, created_at, is_active
                        """,
                        (email, password_hash),
                    )
                    return cur.fetchone()
            except psycopg.errors.UniqueViolation:
                # Relies on the DB UNIQUE constraint the same way
                # PostgresUrlRepository relies on it for short_code --
                # same TOCTOU-avoidance reasoning applies here.
                raise EmailAlreadyRegisteredError(f"Email '{email}' is already registered")

    def get_by_email(self, email: str) -> Optional[UserRecord]:
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=class_row(UserRecord)) as cur:
                cur.execute(
                    """
                    SELECT id, email, password_hash, created_at, is_active
                    FROM users WHERE email = %s AND is_active
                    """,
                    (email,),
                )
                return cur.fetchone()

    def get_by_id(self, user_id: int) -> Optional[UserRecord]:
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=class_row(UserRecord)) as cur:
                cur.execute(
                    """
                    SELECT id, email, password_hash, created_at, is_active
                    FROM users WHERE id = %s AND is_active
                    """,
                    (user_id,),
                )
                return cur.fetchone()
