"""
Cache abstraction for the redirect hot path.

WHY a Protocol here too, same reasoning as UrlRepository: the service
layer should depend on "something that can get/set/delete a string by
key with a TTL," not specifically on Redis. This also makes the
fail-open behavior testable with a fake cache that can simulate
outages without needing to actually kill a Redis process mid-test.

FAIL-OPEN DESIGN (the most important property of this module):
Every method catches redis.RedisError and treats it as a miss/no-op,
logging a warning rather than raising. This is what makes "the system
remains functional if Redis is unavailable" (a stated non-functional
requirement from Phase 0) actually true rather than aspirational --
without this, a Redis outage would take down every redirect, which is
exactly backwards: caching should be a pure performance optimization,
never a new point of failure for the hot path.
"""

import logging
from typing import Optional, Protocol

import redis

logger = logging.getLogger(__name__)


class Cache(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    def delete(self, key: str) -> None: ...


class RedisCache:
    def __init__(self, client: redis.Redis):
        self._client = client

    def get(self, key: str) -> Optional[str]:
        try:
            return self._client.get(key)
        except redis.RedisError as e:
            logger.warning("Redis GET failed for key=%s, treating as cache miss: %s", key, e)
            return None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return  # already-expired data isn't worth caching
        try:
            self._client.set(key, value, ex=ttl_seconds)
        except redis.RedisError as e:
            logger.warning("Redis SET failed for key=%s, continuing without cache: %s", key, e)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except redis.RedisError as e:
            # This is the RISKIEST failure mode in the whole cache
            # layer: if invalidation fails, a deleted URL can keep
            # resolving from a stale cache entry until its TTL
            # expires. We log at a higher severity than the other two
            # methods for exactly that reason -- an operator watching
            # logs should be able to notice this specific failure
            # mode, even though we still don't raise (staying fail-open
            # for the caller).
            logger.error(
                "Redis DELETE (cache invalidation) failed for key=%s -- "
                "stale data may be served until TTL expiry: %s", key, e
            )
