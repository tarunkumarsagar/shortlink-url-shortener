"""
Real RabbitMQ integration tests.

Unlike test_click_event_service.py (fast, fake publisher) this test
uses an ACTUAL pika connection to a REAL RabbitMQ broker, and consumes
messages directly with basic_get -- independent of our own
RabbitMQPublisher abstraction -- to prove redirects actually publish
correctly formed messages to the real queue.

Replaces test_click_events_integration.py from Phase 5: that file
checked click_events rows directly, which was correct when the
redirect wrote to Postgres synchronously. Now that the redirect only
PUBLISHES (the actual DB write happens in the Phase 7 worker, which
doesn't exist yet), the correct thing for THIS phase to verify is that
the message reaches the queue with the right shape -- not that a
click_events row exists (it won't, until Phase 7 is built and running).
"""

import json
import uuid

import pika
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def rabbitmq_channel():
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = connection.channel()
    # Arguments must match what RabbitMQPublisher and the Phase 7
    # worker declare -- see message_queue.py's comment on why RabbitMQ
    # requires consistent arguments across every queue_declare call.
    channel.queue_declare(
        queue=settings.click_events_queue_name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": f"{settings.click_events_queue_name}.dlq",
        },
    )
    # Purge before each test: nothing consumes this queue yet (that's
    # the Phase 7 worker's job), so messages accumulate across test
    # runs and even across separate pytest invocations. Without this,
    # _drain_queue_for_url_id's scan cap could be exceeded by leftover
    # messages from earlier runs, causing false failures unrelated to
    # this test's own behavior.
    channel.queue_purge(queue=settings.click_events_queue_name)
    yield channel
    connection.close()


def unique_code() -> str:
    return f"mq{uuid.uuid4().hex[:10]}"


IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def _drain_queue_for_url_id(channel, url_id: int, max_messages: int = 50, timeout_messages_checked=None):
    """
    Polls the queue with basic_get (no long-lived consumer needed for
    a test) until it finds a message matching the given url_id, or
    gives up. The queue may contain messages from other tests/earlier
    runs, so we can't just grab the first message -- we filter by
    url_id and ack-and-discard anything that doesn't match, to avoid
    tests interfering with each other's assertions.
    """
    for _ in range(max_messages):
        method_frame, properties, body = channel.basic_get(
            queue=settings.click_events_queue_name, auto_ack=True
        )
        if method_frame is None:
            return None
        payload = json.loads(body)
        if payload.get("url_id") == url_id:
            return payload
    return None


def test_redirect_publishes_a_message_to_rabbitmq(client, rabbitmq_channel):
    create_resp = client.post(
        "/api/v1/urls",
        json={"long_url": "https://mq-test.com", "custom_alias": unique_code()},
    )
    short_code = create_resp.json()["short_code"]

    # Need the internal url_id to filter the queue -- fetch it via the
    # metadata endpoint indirectly isn't possible (owner_id/id aren't
    # exposed in UrlResponse), so instead we look up the id straight
    # from Postgres, independent of the app's own code paths.
    import psycopg
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM urls WHERE short_code = %s", (short_code,))
            url_id = cur.fetchone()[0]

    resp = client.get(
        f"/{short_code}",
        follow_redirects=False,
        headers={"User-Agent": IPHONE_UA, "Referer": "https://google.com"},
    )
    assert resp.status_code == 302

    message = _drain_queue_for_url_id(rabbitmq_channel, url_id)

    assert message is not None, "no message published to the click_events queue for this redirect"
    assert message["url_id"] == url_id
    assert message["user_agent"] == IPHONE_UA
    assert message["referrer"] == "https://google.com"
    assert "event_id" in message
    assert "occurred_at" in message


def test_redirect_publishes_raw_unparsed_user_agent(client, rabbitmq_channel):
    """Confirms the message on the wire carries the raw header, not
    pre-parsed device/browser/os fields -- parsing is Phase 7's job."""
    code = unique_code()
    create_resp = client.post(
        "/api/v1/urls", json={"long_url": "https://mq-test-2.com", "custom_alias": code}
    )

    import psycopg
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM urls WHERE short_code = %s", (code,))
            url_id = cur.fetchone()[0]

    client.get(f"/{code}", follow_redirects=False, headers={"User-Agent": IPHONE_UA})

    message = _drain_queue_for_url_id(rabbitmq_channel, url_id)

    assert message is not None
    assert message["user_agent"] == IPHONE_UA
    assert "device_type" not in message
    assert "browser" not in message
    assert "os" not in message
