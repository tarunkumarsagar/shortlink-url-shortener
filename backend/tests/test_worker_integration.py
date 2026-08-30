"""
Real, full end-to-end integration tests for the analytics worker.

Runs the ACTUAL worker (app.workers.worker.run) in a background
thread against the real RabbitMQ and Postgres instances, then
verifies results via independent, direct inspection (raw SQL, raw
queue depth) -- not through our own abstractions. This is the closest
thing in the suite to literally running the production pipeline.
"""

import threading
import time
import uuid

import pika
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.workers.worker import run as run_worker

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def clean_queues():
    """Purge both queues before each test so leftover messages from
    other test files/runs don't cause false positives or negatives."""
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = connection.channel()
    channel.queue_declare(
        queue=settings.click_events_queue_name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": f"{settings.click_events_queue_name}.dlq",
        },
    )
    channel.queue_declare(queue=f"{settings.click_events_queue_name}.dlq", durable=True)
    channel.queue_purge(queue=settings.click_events_queue_name)
    channel.queue_purge(queue=f"{settings.click_events_queue_name}.dlq")
    connection.close()
    yield


def run_worker_briefly(seconds: float = 3.0) -> None:
    """Runs the real worker in a daemon thread for a short, fixed
    window -- long enough to drain a handful of test messages, short
    enough to keep the test suite fast. This is a genuine trade-off:
    a fixed sleep is simpler than a polling wait-for-condition loop,
    at the cost of the test taking a few real seconds regardless of
    how fast processing actually finishes."""
    t = threading.Thread(target=run_worker, daemon=True)
    t.start()
    time.sleep(seconds)


def get_click_event_row(short_code: str):
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ce.event_id, ce.device_type, ce.browser, ce.os, ce.referrer
                FROM click_events ce
                JOIN urls u ON u.id = ce.url_id
                WHERE u.short_code = %s
                """,
                (short_code,),
            )
            return cur.fetchone()


def test_full_pipeline_redirect_to_postgres_row(client, clean_queues):
    """
    THE complete proof: real redirect -> real publish -> real worker
    consumption -> real, correctly-parsed Postgres row. Every layer
    genuinely exercised, none mocked.
    """
    code = f"e2e{uuid.uuid4().hex[:8]}"
    client.post("/api/v1/urls", json={"long_url": "https://full-pipeline-test.com", "custom_alias": code})

    resp = client.get(
        f"/{code}", follow_redirects=False,
        headers={"User-Agent": IPHONE_UA, "Referer": "https://google.com"},
    )
    assert resp.status_code == 302

    run_worker_briefly()

    row = get_click_event_row(code)
    assert row is not None, "no click_events row appeared after running the worker"
    event_id, device_type, browser, os_family, referrer = row
    assert device_type == "mobile"
    assert "Safari" in browser
    assert os_family == "iOS"
    assert referrer == "https://google.com"
    assert event_id is not None


def test_malformed_message_routes_to_dead_letter_queue(clean_queues):
    """
    Publishes a deliberately invalid message directly (bypassing our
    own producer, which would never build one) and confirms the real
    worker routes it to the real DLQ rather than retrying forever or
    silently dropping it.
    """
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = connection.channel()
    channel.basic_publish(
        exchange="", routing_key=settings.click_events_queue_name, body=b"not valid json {{{"
    )
    connection.close()

    run_worker_briefly()

    # Independently verify via a fresh connection: main queue empty,
    # DLQ has exactly the one bad message.
    verify_connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    verify_channel = verify_connection.channel()
    main_queue = verify_channel.queue_declare(
        queue=settings.click_events_queue_name, durable=True, passive=True
    )
    dlq = verify_channel.queue_declare(
        queue=f"{settings.click_events_queue_name}.dlq", durable=True, passive=True
    )
    verify_connection.close()

    assert main_queue.method.message_count == 0
    assert dlq.method.message_count == 1
