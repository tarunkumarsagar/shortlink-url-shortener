"""
Unit tests for RedisCache's fail-open contract. Uses a fake client
that raises redis.RedisError on every call, simulating Redis being
completely down -- this is the property we most need to trust, so it
gets tested in isolation from any real Redis instance.
"""

import redis

from app.core.cache import RedisCache


class AlwaysFailingRedisClient:
    """Simulates Redis being unreachable -- every operation raises."""

    def get(self, key):
        raise redis.ConnectionError("simulated Redis outage")

    def set(self, key, value, ex=None):
        raise redis.ConnectionError("simulated Redis outage")

    def delete(self, key):
        raise redis.ConnectionError("simulated Redis outage")


def test_get_returns_none_on_redis_failure_instead_of_raising():
    cache = RedisCache(AlwaysFailingRedisClient())
    assert cache.get("some-key") is None  # miss, not an exception


def test_set_does_not_raise_on_redis_failure():
    cache = RedisCache(AlwaysFailingRedisClient())
    cache.set("some-key", "some-value", ttl_seconds=60)  # must not raise


def test_delete_does_not_raise_on_redis_failure():
    cache = RedisCache(AlwaysFailingRedisClient())
    cache.delete("some-key")  # must not raise -- see the ERROR-level
    # logging in cache.py for why this specific failure is still
    # surfaced to operators even though it doesn't raise to the caller


def test_set_skips_call_entirely_for_non_positive_ttl():
    """Already-expired data shouldn't even attempt a network call."""
    calls = []

    class RecordingClient:
        def set(self, key, value, ex=None):
            calls.append((key, value, ex))

    cache = RedisCache(RecordingClient())
    cache.set("key", "value", ttl_seconds=0)
    cache.set("key", "value", ttl_seconds=-5)
    assert calls == []
