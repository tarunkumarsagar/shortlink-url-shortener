"""
Click event service.

PHASE 6 REWRITE: this used to write directly to Postgres, synchronously,
inline in the redirect request (Phase 5) -- the module docstring from
that version documented exactly why that was a real, felt cost, and
why it was explicitly a stepping stone, not the design we intended to
keep.

This version does the minimum possible work: build a small JSON
message and publish it to RabbitMQ. No database access. No
user-agent parsing (moved to core/user_agent_parser.py, used by the
Phase 7 worker instead). The actual write to click_events now happens
asynchronously, off the request path entirely, in the Phase 7 worker
that consumes this queue.

DEFENSE IN DEPTH ON FAILURE: RabbitMQPublisher already catches
pika.exceptions.AMQPError internally (see message_queue.py). This
service ALSO wraps the publish call in a broad try/except, on purpose
-- unlike Cache (Phase 4), where we trusted each Cache implementation
to own its own fail-open contract, this method runs via FastAPI's
BackgroundTasks, scheduled AFTER the HTTP response is already sent.
An uncaught exception here can't "fail the redirect" (it already
happened), but it's still the wrong invariant to leave implicit or
dependent on which MessagePublisher implementation happens to be
wired in -- a bug in a future publisher implementation, or a
completely different (non-RabbitMQ) implementation someone plugs in
later, shouldn't be able to violate "click recording never raises."
This was caught by a real test failure during development (a fake
publisher that raised a plain RuntimeError instead of pika's
AMQPError), not written defensively out of guesswork.
"""

import logging
from typing import Optional, Protocol

from app.core.message_queue import build_click_event_message

logger = logging.getLogger(__name__)


class MessagePublisher(Protocol):
    def publish(self, body: bytes) -> None: ...


class ClickEventService:
    def __init__(self, publisher: MessagePublisher):
        self._publisher = publisher

    def record_click(
        self,
        url_id: int,
        user_agent_string: Optional[str],
        referrer: Optional[str],
    ) -> None:
        message = build_click_event_message(
            url_id=url_id,
            user_agent=user_agent_string,
            referrer=referrer,
        )
        try:
            self._publisher.publish(message)
        except Exception as e:
            logger.error(
                "Failed to publish click event for url_id=%s -- event lost: %s",
                url_id, e
            )
