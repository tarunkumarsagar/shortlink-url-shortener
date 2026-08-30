"""
Analytics worker: consumes click_events messages from RabbitMQ and
writes them to Postgres via ClickEventProcessor.

Run standalone (separate process from the API server):
    python3 -m app.workers.worker

ACKNOWLEDGMENT POLICY (see Phase 6/7 checkpoint discussion): manual
ack, sent ONLY after the database write succeeds. auto_ack=False on
the consumer, and we only call basic_ack after process() returns
without raising. This is what gives us at-least-once delivery instead
of at-most-once -- a crash between "received" and "written" leaves the
message unacknowledged, and RabbitMQ redelivers it.

RETRY / DEAD-LETTER POLICY: a MalformedMessageError (bad JSON, missing
fields) is never retried -- it can't succeed no matter how many times
we try, so it's nacked immediately with requeue=False, which (given
the queue's dead-letter configuration below) routes it straight to
click_events.dlq. A ProcessingError (DB write failed) IS retried, up
to MAX_RETRIES times with a short backoff, on the theory that a
transient Postgres blip might clear up -- only after exhausting
retries do we give up and dead-letter it too.

DEAD-LETTER QUEUE SETUP: the main queue is declared with
x-dead-letter-exchange="" and x-dead-letter-routing-key="click_events.dlq"
-- when we nack a message with requeue=False, RabbitMQ automatically
routes it to that queue instead of discarding it, via the default
(nameless) exchange, which routes by queue name. This means a human
can inspect click_events.dlq later to see exactly which messages
permanently failed.
"""

import logging
import time

import pika
import pika.exceptions
from psycopg_pool import ConnectionPool

from app.core.config import settings
from app.repositories.click_event_repository import PostgresClickEventRepository
from app.workers.event_processor import (
    ClickEventProcessor,
    MalformedMessageError,
    ProcessingError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1
DLQ_NAME = f"{settings.click_events_queue_name}.dlq"


def declare_queues(channel) -> None:
    channel.queue_declare(queue=DLQ_NAME, durable=True)
    channel.queue_declare(
        queue=settings.click_events_queue_name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": DLQ_NAME,
        },
    )


def handle_delivery(channel, method, properties, body, processor: ClickEventProcessor) -> None:
    attempt = 0
    while True:
        attempt += 1
        try:
            processor.process(body)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        except MalformedMessageError as e:
            logger.error("Malformed message, routing to DLQ without retry: %s", e)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        except ProcessingError as e:
            if attempt >= MAX_RETRIES:
                logger.error(
                    "Processing failed after %d attempts, routing to DLQ: %s", attempt, e
                )
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            logger.warning(
                "Processing attempt %d/%d failed, retrying in %ds: %s",
                attempt, MAX_RETRIES, RETRY_BACKOFF_SECONDS, e,
            )
            time.sleep(RETRY_BACKOFF_SECONDS)


def connect_with_retry(amqp_url: str, max_attempts: int = 10, initial_delay: float = 2.0):
    """
    Connects to RabbitMQ with exponential backoff, up to max_attempts.

    WHY this exists: a bare pika.BlockingConnection(...) call crashes
    the entire worker process on the very first connection attempt if
    RabbitMQ isn't reachable yet -- a real, observed failure mode
    during docker-compose startup, where a container healthcheck can
    report "healthy" a moment before the broker is truly ready to
    accept client connections. Docker's restart policy would then
    crash-loop the worker if it keeps losing this race. Retrying with
    backoff here is what actually makes the worker resilient to
    normal startup-ordering jitter, rather than depending on
    depends_on/healthcheck timing being perfect.
    """
    delay = initial_delay
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
            logger.info("Connected to RabbitMQ on attempt %d/%d", attempt, max_attempts)
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            last_error = e
            logger.warning(
                "RabbitMQ connection attempt %d/%d failed, retrying in %.1fs: %s",
                attempt, max_attempts, delay, e,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30)  # cap backoff at 30s

    logger.error("Failed to connect to RabbitMQ after %d attempts, giving up", max_attempts)
    raise last_error


def run() -> None:
    pool = ConnectionPool(settings.database_url, min_size=2, max_size=5, open=True)
    repository = PostgresClickEventRepository(pool)
    processor = ClickEventProcessor(repository)

    connection = connect_with_retry(settings.rabbitmq_url)
    channel = connection.channel()
    declare_queues(channel)
    channel.basic_qos(prefetch_count=10)  # don't let one slow worker hoard the whole queue

    def callback(ch, method, properties, body):
        handle_delivery(ch, method, properties, body, processor)

    channel.basic_consume(
        queue=settings.click_events_queue_name,
        on_message_callback=callback,
        auto_ack=False,  # manual ack -- see module docstring
    )

    logger.info("Analytics worker started, consuming from '%s'", settings.click_events_queue_name)
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        connection.close()
        pool.close()


if __name__ == "__main__":
    run()
