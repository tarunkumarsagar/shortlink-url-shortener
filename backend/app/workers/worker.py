
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

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
RECONNECT_DELAY_SECONDS = 5

# Set once the consumer thread has successfully connected and started
# consuming; cleared the moment that connection is lost for any reason
# (including a graceful reconnect-in-progress). The health endpoint
# reads this directly -- it is the single source of truth for "is the
# worker actually doing its job right now."
_consumer_connected = threading.Event()


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
    accept client connections. Retrying with backoff here is what
    actually makes the worker resilient to normal startup-ordering
    jitter, rather than depending on depends_on/healthcheck timing
    being perfect.
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


def _consume_once(pool: ConnectionPool) -> None:
    """
    One full connect-declare-consume lifecycle. Runs until the
    connection is lost or an unrecoverable error occurs, at which
    point it raises back to the caller (run_consumer_forever), which
    decides whether/how to reconnect. Separated from run_consumer_forever
    so each attempt is a clean, fully torn-down connection -- no
    partially-initialized state carried across reconnect attempts.
    """
    repository = PostgresClickEventRepository(pool)
    processor = ClickEventProcessor(repository)

    connection = connect_with_retry(settings.rabbitmq_url)
    try:
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

        logger.info(
            "Analytics worker started, consuming from '%s'", settings.click_events_queue_name
        )
        _consumer_connected.set()
        channel.start_consuming()
    finally:
        _consumer_connected.clear()
        connection.close()


def run_consumer_forever() -> None:
    """
    Outer resilience loop: connect_with_retry() already handles the
    INITIAL connection race (Phase-startup jitter). This loop handles
    the SEPARATE case of losing an already-established connection
    later (broker restart, network blip, free-tier platform hiccup) --
    without it, a mid-lifetime disconnect would permanently kill the
    consumer thread while the process (and its health endpoint) kept
    running, silently stopping analytics processing with no crash to
    notice. On any disconnect, it waits RECONNECT_DELAY_SECONDS and
    tries again, indefinitely.
    """
    pool = ConnectionPool(settings.database_url, min_size=2, max_size=5, open=True)
    try:
        while True:
            try:
                _consume_once(pool)
            except pika.exceptions.AMQPError as e:
                logger.error(
                    "Consumer connection lost, reconnecting in %ds: %s",
                    RECONNECT_DELAY_SECONDS, e,
                )
                time.sleep(RECONNECT_DELAY_SECONDS)
            except KeyboardInterrupt:
                logger.info("Shutting down (KeyboardInterrupt)")
                return
    finally:
        pool.close()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            healthy = _consumer_connected.is_set()
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = {"status": "ok" if healthy else "unhealthy", "consumer_connected": healthy}
            self.wfile.write(json.dumps(body).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # silence default per-request access logging; we have our own logger above


def run_health_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("Health check server listening on port %d", port)
    server.serve_forever()


def run() -> None:
    """
    Entry point. Runs the RabbitMQ consumer in a background (daemon)
    thread and the HTTP health server in the main thread. If you don't
    need the HTTP health endpoint (e.g. running this as a genuine
    always-on background worker with no platform requiring an HTTP
    port), the consumer thread alone is functionally equivalent to the
    pre-health-check version of this module.
    """
    consumer_thread = threading.Thread(target=run_consumer_forever, daemon=True)
    consumer_thread.start()
    run_health_server()


if __name__ == "__main__":
    run()
