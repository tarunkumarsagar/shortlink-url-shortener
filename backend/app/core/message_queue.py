"""
Message publisher abstraction for the click-events queue.

Same Protocol-based design as Cache (Phase 4) and UrlRepository
(Phase 1/2): the service layer depends on "something that can publish
bytes," not specifically on RabbitMQ/pika.

FAIL-OPEN, same philosophy as Redis: if the broker is unreachable at
publish time, we log and drop the event rather than blocking or
failing the redirect. See the Phase 5/6 checkpoint discussion for why
this narrow window (publish-time only) is still an accepted,
documented trade-off -- everything AFTER a successful publish is now
reliably handled by the broker + Phase 7's worker, which is the actual
improvement over Phase 5.

CONNECTION STRATEGY: pika's BlockingConnection is not safe to share
across threads, and FastAPI runs sync route handlers in a thread pool
-- so sharing one long-lived connection across concurrent requests
would be a real bug (interleaved channel usage from multiple threads).
We open a short-lived connection per publish instead: simpler and
correct under this concurrency model, at the cost of per-publish
connection overhead. A production system at higher throughput would
use a connection pool (e.g. one connection per worker thread) or an
async AMQP client -- documented here as a concrete, known optimization
we're deliberately not building yet, not an oversight.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol

import pika
import pika.exceptions

logger = logging.getLogger(__name__)


class MessagePublisher(Protocol):
    def publish(self, body: bytes) -> None: ...


class RabbitMQPublisher:
    def __init__(self, amqp_url: str, queue_name: str):
        self._amqp_url = amqp_url
        self._queue_name = queue_name

    def publish(self, body: bytes) -> None:
        try:
            connection = pika.BlockingConnection(pika.URLParameters(self._amqp_url))
            try:
                channel = connection.channel()
                # IMPORTANT: these queue_declare arguments (durable +
                # the dead-letter config) MUST exactly match what
                # app/workers/worker.py declares -- RabbitMQ requires
                # a queue's arguments to be identical every time it's
                # declared, and raises a channel-level
                # PRECONDITION_FAILED error if they don't match,
                # regardless of which side (producer or consumer)
                # happens to create the queue first. Declaring the
                # same config in both places, rather than only in the
                # worker, is what makes this safe regardless of start
                # order.
                channel.queue_declare(
                    queue=self._queue_name,
                    durable=True,
                    arguments={
                        "x-dead-letter-exchange": "",
                        "x-dead-letter-routing-key": f"{self._queue_name}.dlq",
                    },
                )
                channel.basic_publish(
                    exchange="",  # default exchange: routes directly to the named queue
                    routing_key=self._queue_name,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # persistent: survives a broker restart
                        content_type="application/json",
                    ),
                )
            finally:
                connection.close()
        except pika.exceptions.AMQPError as e:
            logger.error(
                "Failed to publish click event to RabbitMQ -- event lost "
                "(this is the accepted, narrow best-effort window at "
                "publish time; see Phase 5/6 checkpoint discussion): %s", e
            )


def build_click_event_message(
    url_id: int,
    user_agent: Optional[str],
    referrer: Optional[str],
) -> bytes:
    """
    The event schema published to the queue. Deliberately raw/unparsed
    (user_agent as the original header string, not pre-classified) --
    parsing happens in the Phase 7 worker, not here (see
    core/user_agent_parser.py's module docstring for why).

    event_id: a UUID unique to this specific publish, included
    specifically so the Phase 7 worker can implement idempotent
    processing (deduplicate if this message is ever redelivered by
    RabbitMQ's at-least-once guarantee -- see Phase 5/6 checkpoint Q1).
    """
    return json.dumps(
        {
            "event_id": str(uuid.uuid4()),
            "url_id": url_id,
            "user_agent": user_agent,
            "referrer": referrer,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
    ).encode("utf-8")
