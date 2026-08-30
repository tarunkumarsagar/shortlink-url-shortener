"""
Click event processor -- the worker's business logic, deliberately
separated from the raw pika consume loop (worker.py), same philosophy
as UrlService being separate from the FastAPI route that calls it.
This is what makes the worker's actual behavior unit-testable without
needing a running consume loop for every test.
"""

import json
import logging
from typing import Protocol

from app.core.user_agent_parser import parse_user_agent

logger = logging.getLogger(__name__)


class ClickEventRepository(Protocol):
    def insert_idempotent(
        self,
        event_id: str,
        url_id: int,
        referrer,
        user_agent,
        device_type,
        browser,
        os,
    ) -> bool: ...


class MalformedMessageError(Exception):
    """
    Raised for a message that can never succeed no matter how many
    times it's retried (invalid JSON, missing required fields). The
    worker routes these to the dead-letter queue immediately rather
    than retrying -- retrying a permanently-broken message is pure
    wasted work and would loop forever without a DLQ.
    """


class ProcessingError(Exception):
    """
    Raised for a TRANSIENT failure (e.g. Postgres briefly unreachable)
    that might succeed on retry. The worker retries a bounded number
    of times before giving up and routing to the dead-letter queue --
    see worker.py for the retry policy.
    """


class ClickEventProcessor:
    def __init__(self, repository: ClickEventRepository):
        self._repository = repository

    def process(self, raw_message: bytes) -> None:
        try:
            payload = json.loads(raw_message)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise MalformedMessageError(f"Message body is not valid JSON: {e}")

        required_fields = ("event_id", "url_id")
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise MalformedMessageError(f"Message missing required fields: {missing}")

        parsed_ua = parse_user_agent(payload.get("user_agent"))

        try:
            inserted = self._repository.insert_idempotent(
                event_id=payload["event_id"],
                url_id=payload["url_id"],
                referrer=payload.get("referrer"),
                user_agent=payload.get("user_agent"),
                device_type=parsed_ua.device_type,
                browser=parsed_ua.browser,
                os=parsed_ua.os,
            )
        except Exception as e:
            # Any repository/DB-level failure is treated as transient
            # and retryable -- a bad Postgres connection right now
            # doesn't mean it'll still be bad on the next attempt.
            raise ProcessingError(f"Failed to write click event to database: {e}")

        if not inserted:
            logger.info(
                "Duplicate click event %s for url_id=%s -- already processed, "
                "skipping (this is RabbitMQ's at-least-once delivery working "
                "as designed, not an error)",
                payload["event_id"], payload["url_id"],
            )
