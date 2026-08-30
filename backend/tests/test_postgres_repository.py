"""
Integration tests against a real PostgreSQL instance.

Unlike test_url_service.py (pure in-memory, no I/O) these tests
require a running Postgres with the migrations applied. They are
slower and are the ONLY place in the suite that can honestly test the
concurrency guarantee our schema provides (see test_true_concurrent_
insert_race below) -- an in-memory dict behind a single GIL cannot
reproduce this, which is exactly the gap we called out at the end of
Phase 1.
"""

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from app.core.config import settings
from app.repositories.exceptions import ShortCodeCollisionError
from app.repositories.postgres_url_repository import PostgresUrlRepository


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(settings.database_url, min_size=2, max_size=10, open=True)
    yield p
    p.close()


@pytest.fixture
def repo(pool):
    return PostgresUrlRepository(pool)


def unique_code() -> str:
    """Avoid cross-test collisions in a shared table -- each test gets
    its own random code namespace rather than relying on truncating
    the table between tests (which would slow the suite down and
    couple tests to teardown ordering)."""
    return f"t{uuid.uuid4().hex[:10]}"


def test_insert_and_get_round_trip(repo):
    code = unique_code()
    inserted = repo.insert(code, "https://example.com/pg-test")
    fetched = repo.get(code)
    assert fetched is not None
    assert fetched.long_url == "https://example.com/pg-test"
    assert fetched.id == inserted.id


def test_get_unknown_code_returns_none(repo):
    assert repo.get(unique_code()) is None


def test_duplicate_insert_raises_collision_error(repo):
    """This is the DIRECT test of the fix from the Phase 1/2
    checkpoint discussion: the second insert must fail cleanly via the
    UNIQUE constraint, mapped to our repository-level exception."""
    code = unique_code()
    repo.insert(code, "https://a.com")
    with pytest.raises(ShortCodeCollisionError):
        repo.insert(code, "https://b.com")


def test_soft_delete_hides_url_from_get(repo):
    code = unique_code()
    repo.insert(code, "https://example.com")
    assert repo.delete(code) is True
    assert repo.get(code) is None  # filtered by is_active in the query


def test_delete_nonexistent_returns_false(repo):
    assert repo.delete(unique_code()) is False


def test_expires_at_is_persisted_correctly(repo):
    code = unique_code()
    expiry = datetime.now(timezone.utc) + timedelta(days=7)
    repo.insert(code, "https://example.com", expires_at=expiry)
    record = repo.get(code)
    # Postgres TIMESTAMPTZ round-trips to within microsecond precision;
    # compare at second granularity to avoid flaky sub-microsecond diffs.
    assert abs((record.expires_at - expiry).total_seconds()) < 1


def test_true_concurrent_insert_race(pool):
    """
    THE test we explicitly could not write honestly in Phase 1.

    We spin up two real OS threads, each with its OWN Postgres
    connection from the pool, and have both attempt to INSERT the
    same short_code as close to simultaneously as we can arrange
    (a threading.Barrier forces them to fire together rather than
    sequentially). This exercises actual concurrent access to the
    database, not just concurrent-looking application code.

    Expected outcome, guaranteed by the UNIQUE constraint: exactly one
    thread succeeds, the other gets a UniqueViolation -> our
    ShortCodeCollisionError. No silent data corruption, no duplicate
    rows, regardless of which thread the OS scheduler favors.
    """
    code = unique_code()
    results = {}
    barrier = threading.Barrier(2)

    def attempt_insert(thread_name: str, long_url: str):
        repo = PostgresUrlRepository(pool)
        barrier.wait()  # both threads block here, then release together
        try:
            repo.insert(code, long_url)
            results[thread_name] = "success"
        except ShortCodeCollisionError:
            results[thread_name] = "collision"

    t1 = threading.Thread(target=attempt_insert, args=("t1", "https://winner-a.com"))
    t2 = threading.Thread(target=attempt_insert, args=("t2", "https://winner-b.com"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    outcomes = set(results.values())
    assert outcomes == {"success", "collision"}, (
        f"Expected exactly one success and one collision, got: {results}"
    )

    # And critically: exactly ONE row exists for this code, not zero,
    # not two, not a corrupted mix.
    repo = PostgresUrlRepository(pool)
    record = repo.get(code)
    assert record is not None
    assert record.long_url in ("https://winner-a.com", "https://winner-b.com")
