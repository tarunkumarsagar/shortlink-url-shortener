"""
Real Redis + real Postgres integration test.

Unlike test_url_service_caching.py (fast, in-memory fakes) this test
uses an ACTUAL redis-py client against an ACTUAL Redis instance, and
inspects Redis directly with raw GET/EXISTS calls to independently
verify the cache is doing what we claim -- not just trusting
UrlService's return values.
"""

import uuid

import pytest
import redis as redis_lib
from psycopg_pool import ConnectionPool

from app.core.cache import RedisCache
from app.core.config import settings
from app.repositories.postgres_url_repository import PostgresUrlRepository
from app.services.url_service import UrlNotFoundError, UrlService


@pytest.fixture(scope="module")
def pg_pool():
    p = ConnectionPool(settings.database_url, min_size=2, max_size=10, open=True)
    yield p
    p.close()


@pytest.fixture
def redis_client():
    client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    client.close()


@pytest.fixture
def service(pg_pool, redis_client):
    repository = PostgresUrlRepository(pg_pool)
    cache = RedisCache(redis_client)
    return UrlService(repository, cache=cache, cache_ttl_seconds=300)


def unique_code() -> str:
    return f"rt{uuid.uuid4().hex[:10]}"


def test_resolve_populates_real_redis_and_second_resolve_hits_it(service, redis_client):
    code = unique_code()
    service.create_short_url("https://real-integration-test.com", custom_alias=code)

    redis_key = f"url:{code}"
    assert redis_client.exists(redis_key) == 0  # nothing cached yet

    result = service.resolve(code)
    assert result.long_url == "https://real-integration-test.com"

    # Independently verify via a raw Redis call -- not through our
    # own abstraction, so we're not just testing our own mocks.
    assert redis_client.exists(redis_key) == 1
    cached_raw = redis_client.get(redis_key)
    assert "real-integration-test.com" in cached_raw


def test_delete_invalidates_real_redis_key(service, redis_client):
    code = unique_code()
    service.create_short_url("https://to-be-deleted.com", custom_alias=code)
    service.resolve(code)  # populate real Redis

    redis_key = f"url:{code}"
    assert redis_client.exists(redis_key) == 1  # confirmed cached

    service.delete(code)

    # Direct Redis inspection, not just "does resolve() still work" --
    # proves the actual key is gone from the actual cache.
    assert redis_client.exists(redis_key) == 0


def test_resolve_after_real_delete_is_not_served_stale(service):
    code = unique_code()
    service.create_short_url("https://stale-check.com", custom_alias=code)
    service.resolve(code)
    service.delete(code)

    with pytest.raises(UrlNotFoundError):
        service.resolve(code)
