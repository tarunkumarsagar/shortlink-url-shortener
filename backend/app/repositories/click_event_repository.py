"""
Click event repository. Insert-heavy, read-light in this phase (real
analytics query patterns arrive in Phase 13's dashboard work) -- for
now, get_count_for_url and get_recent_for_url exist mainly to make
this phase's behavior independently verifiable in tests, not to
front a dashboard yet.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from psycopg.rows import class_row
from psycopg_pool import ConnectionPool


@dataclass
class ClickEventRecord:
    id: int
    url_id: int
    clicked_at: datetime
    referrer: Optional[str]
    user_agent: Optional[str]
    device_type: Optional[str]
    browser: Optional[str]
    os: Optional[str]
    country: Optional[str]
    event_id: Optional[str] = None


class PostgresClickEventRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def insert(
        self,
        url_id: int,
        referrer: Optional[str],
        user_agent: Optional[str],
        device_type: Optional[str],
        browser: Optional[str],
        os: Optional[str],
    ) -> ClickEventRecord:
        """
        Non-idempotent insert. Retained for direct/manual use and for
        the tests that don't care about deduplication -- the Phase 7
        worker uses insert_idempotent() below instead, since it's the
        one path that must survive RabbitMQ redelivery correctly.
        """
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=class_row(ClickEventRecord)) as cur:
                cur.execute(
                    """
                    INSERT INTO click_events (url_id, referrer, user_agent, device_type, browser, os)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, url_id, clicked_at, referrer, user_agent, device_type, browser, os, country, event_id
                    """,
                    (url_id, referrer, user_agent, device_type, browser, os),
                )
                return cur.fetchone()

    def insert_idempotent(
        self,
        event_id: str,
        url_id: int,
        referrer: Optional[str],
        user_agent: Optional[str],
        device_type: Optional[str],
        browser: Optional[str],
        os: Optional[str],
    ) -> bool:
        """
        THE mechanism from the Phase 6/7 checkpoint discussion:
        ON CONFLICT (event_id) DO NOTHING makes this safe to call
        twice with the same event_id -- exactly what happens when
        RabbitMQ redelivers a message the worker already processed
        but hadn't yet acked.

        Returns True if a new row was actually inserted, False if
        this event_id was already present (a harmless duplicate
        delivery, not an error) -- the caller (the worker) treats
        both outcomes as "successfully processed" and acks either way;
        the return value exists for observability/testing, not to
        change the ack decision.
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO click_events (event_id, url_id, referrer, user_agent, device_type, browser, os)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (event_id, url_id, referrer, user_agent, device_type, browser, os),
                )
                return cur.rowcount > 0

    def get_count_for_url(self, url_id: int) -> int:
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM click_events WHERE url_id = %s", (url_id,))
                return cur.fetchone()[0]

    def get_recent_for_url(self, url_id: int, limit: int = 10) -> list[ClickEventRecord]:
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=class_row(ClickEventRecord)) as cur:
                cur.execute(
                    """
                    SELECT id, url_id, clicked_at, referrer, user_agent, device_type, browser, os, country, event_id
                    FROM click_events
                    WHERE url_id = %s
                    ORDER BY clicked_at DESC
                    LIMIT %s
                    """,
                    (url_id, limit),
                )
                return cur.fetchall()
