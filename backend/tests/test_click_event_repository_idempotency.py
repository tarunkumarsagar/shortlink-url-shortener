"""
Real Postgres integration test for PostgresClickEventRepository's
idempotent insert -- proves the UNIQUE constraint on event_id actually
does what the Phase 6/7 checkpoint discussion claims: a second insert
with the same event_id is a safe no-op, not a duplicate row or an
error.
"""

import uuid

import pytest
from psycopg_pool import ConnectionPool

from app.core.config import settings
from app.repositories.click_event_repository import PostgresClickEventRepository
from app.repositories.postgres_url_repository import PostgresUrlRepository


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(settings.database_url, min_size=2, max_size=10, open=True)
    yield p
    p.close()


@pytest.fixture
def click_repo(pool):
    return PostgresClickEventRepository(pool)


@pytest.fixture
def url_id(pool):
    """A real urls.id to satisfy click_events' FK constraint."""
    url_repo = PostgresUrlRepository(pool)
    code = f"idem{uuid.uuid4().hex[:8]}"
    record = url_repo.insert(code, "https://idempotency-test.com")
    return record.id


def test_first_insert_with_new_event_id_succeeds(click_repo, url_id):
    event_id = str(uuid.uuid4())
    result = click_repo.insert_idempotent(
        event_id=event_id, url_id=url_id, referrer=None, user_agent=None,
        device_type=None, browser=None, os=None,
    )
    assert result is True  # a new row was genuinely inserted


def test_second_insert_with_same_event_id_is_a_safe_no_op(click_repo, url_id):
    """
    THE core proof behind the Phase 6/7 checkpoint answer: simulate
    RabbitMQ redelivering the exact same message (same event_id) by
    calling insert_idempotent twice with identical arguments. The
    second call must return False (no row inserted) and, critically,
    must NOT raise or create a duplicate row.
    """
    event_id = str(uuid.uuid4())

    first_result = click_repo.insert_idempotent(
        event_id=event_id, url_id=url_id, referrer="https://a.com", user_agent="ua-a",
        device_type="mobile", browser="Safari", os="iOS",
    )
    second_result = click_repo.insert_idempotent(
        event_id=event_id, url_id=url_id, referrer="https://a.com", user_agent="ua-a",
        device_type="mobile", browser="Safari", os="iOS",
    )

    assert first_result is True
    assert second_result is False  # ON CONFLICT DO NOTHING kicked in

    # Independently verify via direct SQL: exactly ONE row for this
    # event_id, not zero, not two.
    assert click_repo.get_count_for_url(url_id) == 1


def test_ten_redeliveries_of_the_same_event_produce_exactly_one_row(click_repo, url_id):
    """More aggressive version of the above -- simulates a worse-case
    redelivery storm (e.g. a flapping consumer connection) to build
    real confidence the guarantee holds under repetition, not just
    once."""
    event_id = str(uuid.uuid4())

    for _ in range(10):
        click_repo.insert_idempotent(
            event_id=event_id, url_id=url_id, referrer=None, user_agent=None,
            device_type=None, browser=None, os=None,
        )

    assert click_repo.get_count_for_url(url_id) == 1
