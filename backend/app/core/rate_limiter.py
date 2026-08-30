"""
Rate limiting: sliding window, backed by Redis sorted sets.

WHY sliding window over fixed window: fixed window (e.g. "20 requests
per calendar minute") allows a burst of 40 requests right at a minute
boundary (20 at 0:59, 20 more at 1:00) -- twice the intended limit.
Sliding window avoids this by looking at a continuously moving
60-second lookback from "now", not a fixed clock-aligned bucket.

WHY Redis, not an in-process counter: rate limits must be enforced
correctly across multiple backend instances (our stated horizontal-
scaling goal). An in-process dict of counters would let each instance
independently allow its own full quota -- N instances would multiply
the effective limit by N. Redis is the one thing every instance
shares, making it the natural place to keep this shared state.

IMPLEMENTATION: a Redis sorted set per identity (IP or user id), where
each member is a unique request marker and its score is the request's
timestamp. On each check: remove entries older than the window
(ZREMRANGEBYSCORE), count what's left (ZCARD), and if under the limit,
add the new entry (ZADD) with a TTL on the key so abandoned keys don't
leak memory forever.

FAIL-OPEN, same philosophy as Cache and MessagePublisher: if Redis is
unreachable, we allow the request rather than blocking all traffic --
a rate limiter that fails closed would turn a Redis outage into a
total API outage, a worse failure mode than temporarily having no
rate limiting.
"""

import logging
import time
import uuid

import redis

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self, client: redis.Redis):
        self._client = client

    def is_allowed(self, identity: str, limit: int) -> bool:
        key = f"ratelimit:{identity}"
        now = time.time()
        window_start = now - WINDOW_SECONDS

        try:
            pipe = self._client.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            _, current_count = pipe.execute()

            if current_count >= limit:
                return False

            pipe = self._client.pipeline()
            pipe.zadd(key, {str(uuid.uuid4()): now})
            pipe.expire(key, WINDOW_SECONDS)
            pipe.execute()
            return True
        except redis.RedisError as e:
            logger.warning(
                "Rate limiter Redis call failed, failing OPEN (allowing "
                "the request) for identity=%s: %s", identity, e
            )
            return True
