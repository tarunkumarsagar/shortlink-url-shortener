from datetime import datetime, timedelta, timezone

import pytest

from app.repositories.url_repository import InMemoryUrlRepository
from app.services.url_service import UrlExpiredError, UrlNotFoundError, UrlService


class InMemoryCache:
    """A real, working (non-Redis) cache for testing UrlService's
    cache-aside logic in isolation from any actual Redis instance."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, str, int]] = []
        self.delete_calls: list[str] = []

    def get(self, key):
        self.get_calls.append(key)
        return self._store.get(key)

    def set(self, key, value, ttl_seconds):
        self.set_calls.append((key, value, ttl_seconds))
        self._store[key] = value

    def delete(self, key):
        self.delete_calls.append(key)
        self._store.pop(key, None)


class CountingRepository(InMemoryUrlRepository):
    """Wraps InMemoryUrlRepository, counting get() calls so tests can
    assert the cache is actually being consulted BEFORE the repository,
    not just that the final answer happens to be correct."""

    def __init__(self):
        super().__init__()
        self.get_call_count = 0

    def get(self, short_code):
        self.get_call_count += 1
        return super().get(short_code)


@pytest.fixture
def repo():
    return CountingRepository()


@pytest.fixture
def cache():
    return InMemoryCache()


@pytest.fixture
def service(repo, cache):
    return UrlService(repo, cache=cache, cache_ttl_seconds=300)


def test_first_resolve_is_a_cache_miss_and_populates_cache(service, repo, cache):
    record = service.create_short_url("https://example.com")
    # create_short_url doesn't pre-populate the cache (lazy, cache-aside)
    assert cache.set_calls == []

    result = service.resolve(record.short_code)

    assert result.long_url == "https://example.com"
    assert repo.get_call_count == 1  # went to the repository on the miss
    assert len(cache.set_calls) == 1  # and populated the cache after


def test_second_resolve_is_a_cache_hit_and_skips_repository(service, repo, cache):
    """THE core proof of cache-aside: after the first resolve populates
    the cache, a second resolve for the same code must NOT call the
    repository again."""
    record = service.create_short_url("https://example.com")
    service.resolve(record.short_code)  # first call: miss, populates cache
    assert repo.get_call_count == 1

    service.resolve(record.short_code)  # second call: should be a hit
    assert repo.get_call_count == 1  # UNCHANGED -- repository not touched again


def test_delete_invalidates_cache_entry(service, repo, cache):
    """THE mandatory-invalidation test from the Phase 3/4 checkpoint
    discussion: after delete, the cache entry must be gone, not just
    the Postgres row."""
    record = service.create_short_url("https://example.com")
    service.resolve(record.short_code)  # populate the cache
    assert cache.get(f"url:{record.short_code}") is not None

    service.delete(record.short_code)

    assert cache.get(f"url:{record.short_code}") is None
    assert f"url:{record.short_code}" in cache.delete_calls


def test_deleted_url_is_not_served_from_stale_cache_after_invalidation(service, repo, cache):
    """End-to-end proof within the service layer: create, cache via
    resolve, delete, then attempt resolve again -- must correctly 404,
    not return the stale cached destination."""
    record = service.create_short_url("https://example.com")
    service.resolve(record.short_code)
    service.delete(record.short_code)

    with pytest.raises(UrlNotFoundError):
        service.resolve(record.short_code)


def test_cache_ttl_is_capped_by_expires_at(service, repo, cache):
    """A URL expiring in 30 seconds should never get a 300-second
    (default) cache TTL -- otherwise the cache could outlive the URL's
    own validity."""
    soon = datetime.now(timezone.utc) + timedelta(seconds=30)
    record = service.create_short_url("https://example.com", expires_at=soon)

    service.resolve(record.short_code)

    (_, _, ttl_used) = cache.set_calls[0]
    assert ttl_used <= 30


def test_resolve_works_correctly_with_no_cache_configured(repo):
    """Proves UrlService degrades cleanly to direct-repository behavior
    when constructed without a cache at all (cache=None) -- this is the
    actual code path exercised when a real Redis outage is severe
    enough that even RedisCache's fail-open wrapper isn't in the loop,
    and it's also simply the correct behavior for any caller that
    doesn't want caching."""
    service = UrlService(repo, cache=None)
    record = service.create_short_url("https://example.com")
    result = service.resolve(record.short_code)
    assert result.long_url == "https://example.com"
