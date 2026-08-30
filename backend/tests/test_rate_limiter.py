import uuid

import pytest
import redis

from app.core.rate_limiter import RateLimiter


@pytest.fixture
def redis_client():
    from app.core.config import settings
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    client.close()


def unique_identity() -> str:
    return f"test:{uuid.uuid4().hex[:10]}"


def test_allows_requests_under_the_limit(redis_client):
    limiter = RateLimiter(redis_client)
    identity = unique_identity()
    results = [limiter.is_allowed(identity, limit=5) for _ in range(5)]
    assert all(results)


def test_blocks_requests_over_the_limit(redis_client):
    limiter = RateLimiter(redis_client)
    identity = unique_identity()

    allowed = [limiter.is_allowed(identity, limit=3) for _ in range(3)]
    assert all(allowed)

    blocked = limiter.is_allowed(identity, limit=3)
    assert blocked is False


def test_different_identities_have_independent_limits(redis_client):
    limiter = RateLimiter(redis_client)
    identity_a = unique_identity()
    identity_b = unique_identity()

    for _ in range(3):
        limiter.is_allowed(identity_a, limit=3)

    assert limiter.is_allowed(identity_b, limit=3) is True


def test_fails_open_when_redis_is_unreachable():
    class AlwaysFailingClient:
        def pipeline(self):
            raise redis.ConnectionError("simulated outage")

    limiter = RateLimiter(AlwaysFailingClient())
    assert limiter.is_allowed("some-identity", limit=1) is True


def test_real_api_returns_429_after_exceeding_anonymous_limit():
    """
    End-to-end proof against the REAL app (no dependency overrides),
    real Redis: hammer the create endpoint past the configured
    anonymous limit and confirm a 429, using a unique fake client IP
    (via X-Forwarded-For isn't wired up -- we key on request.client.host,
    which TestClient reports consistently as 'testclient', so this
    test's IP-bucket is shared across the whole test run; we use a very
    low override via monkeypatching settings for a deterministic,
    fast test instead of hammering the real default of 20/min).
    """
    from fastapi.testclient import TestClient
    from app.api import dependencies
    from app.main import app

    original_limit = dependencies.settings.rate_limit_per_minute_anonymous
    dependencies.settings.rate_limit_per_minute_anonymous = 3
    try:
        client = TestClient(app)
        # Clear any prior state for this identity (TestClient's fixed
        # 'testclient' host) so this test isn't order-dependent.
        dependencies._redis_client.delete("ratelimit:ip:testclient")

        statuses = []
        for _ in range(5):
            resp = client.post("/api/v1/urls", json={"long_url": "https://ratelimit-test.com"})
            statuses.append(resp.status_code)

        assert statuses[:3] == [201, 201, 201]
        assert 429 in statuses[3:]
    finally:
        dependencies.settings.rate_limit_per_minute_anonymous = original_limit
        dependencies._redis_client.delete("ratelimit:ip:testclient")
