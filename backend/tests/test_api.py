import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    enforce_create_url_rate_limit,
    get_click_event_service,
    get_url_service,
)
from app.main import app
from app.repositories.url_repository import InMemoryUrlRepository
from app.services.click_event_service import ClickEventService
from app.services.url_service import UrlService


class InMemoryMessagePublisher:
    """
    Fake satisfying ClickEventService's MessagePublisher Protocol,
    keeping these API-contract tests independent of a real RabbitMQ
    broker. Records published message bodies (raw bytes, exactly what
    a real publisher would send) so tests CAN assert on them, without
    requiring a running broker.
    """

    def __init__(self):
        self.published: list[bytes] = []

    def publish(self, body: bytes) -> None:
        self.published.append(body)


@pytest.fixture(autouse=True)
def use_in_memory_service():
    """
    These are API-CONTRACT tests (status codes, response shapes,
    HTTP-level behavior) -- they should not depend on a running
    Postgres instance, which would make this file slow and couple our
    fast test suite to external infrastructure being up. FastAPI's
    dependency_overrides lets us swap in a fresh InMemoryUrlRepository
    per test while exercising the REAL route/service code, and the
    override is reset after each test via the finally block so tests
    stay isolated from each other.

    The Postgres-specific behavior (the UNIQUE constraint, the real
    concurrency guarantee) is tested directly and honestly in
    tests/test_postgres_repository.py instead -- that's the correct
    place for it, not here.
    """
    repository = InMemoryUrlRepository()
    service = UrlService(repository)

    click_repository = InMemoryMessagePublisher()
    click_service = ClickEventService(click_repository)

    def override_url_service():
        yield service

    def override_click_service():
        yield click_service

    def override_rate_limit():
        return None  # no-op: keeps these tests independent of Redis

    app.dependency_overrides[get_url_service] = override_url_service
    app.dependency_overrides[get_click_event_service] = override_click_service
    app.dependency_overrides[enforce_create_url_rate_limit] = override_rate_limit
    try:
        yield
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_short_url_returns_201(client):
    response = client.post("/api/v1/urls", json={"long_url": "https://example.com/page"})
    assert response.status_code == 201
    body = response.json()
    assert len(body["short_code"]) == 7
    assert body["long_url"] == "https://example.com/page"


def test_redirect_returns_302_with_correct_location(client):
    create_resp = client.post("/api/v1/urls", json={"long_url": "https://example.com/target"})
    code = create_resp.json()["short_code"]

    redirect_resp = client.get(f"/{code}", follow_redirects=False)
    assert redirect_resp.status_code == 302
    assert redirect_resp.headers["location"] == "https://example.com/target"


def test_unknown_short_code_returns_404(client):
    response = client.get("/nonexistent1", follow_redirects=False)
    assert response.status_code == 404


def test_custom_alias_conflict_returns_409(client):
    client.post("/api/v1/urls", json={"long_url": "https://a.com", "custom_alias": "conflict"})
    response = client.post(
        "/api/v1/urls", json={"long_url": "https://b.com", "custom_alias": "conflict"}
    )
    assert response.status_code == 409


def test_invalid_url_returns_400(client):
    response = client.post("/api/v1/urls", json={"long_url": "not-a-valid-url"})
    assert response.status_code == 400


def test_malformed_request_body_returns_422(client):
    """Missing the required `long_url` field entirely -- this is
    Pydantic's job, not our service layer's, which is exactly why we
    expect a 422 (validation) rather than our custom 400 (semantic
    business-rule violation)."""
    response = client.post("/api/v1/urls", json={})
    assert response.status_code == 422


def test_delete_then_redirect_returns_404(client):
    create_resp = client.post("/api/v1/urls", json={"long_url": "https://example.com"})
    code = create_resp.json()["short_code"]

    delete_resp = client.delete(f"/api/v1/urls/{code}")
    assert delete_resp.status_code == 204

    redirect_resp = client.get(f"/{code}", follow_redirects=False)
    assert redirect_resp.status_code == 404


def test_concurrent_custom_alias_requests_only_one_succeeds(client):
    """
    THE concurrency test described in the project spec (section 16):
    two 'simultaneous' requests for the same custom alias. We can't
    truly test thread-level race conditions against an in-memory dict
    behind FastAPI's TestClient (it's synchronous, single-threaded from
    the test's perspective) -- what we CAN and DO verify here is the
    OUTCOME our design guarantees: exactly one of two identical
    requests succeeds, the other gets a clean 409, and no data is
    corrupted (no silent overwrite). The true concurrent-write race
    condition is only meaningfully testable once we're against a real
    Postgres instance with a UNIQUE constraint (Phase 2) -- we'll add
    a genuine multi-threaded/multi-process test then.
    """
    payload_a = {"long_url": "https://a.com", "custom_alias": "race-alias"}
    payload_b = {"long_url": "https://b.com", "custom_alias": "race-alias"}

    resp_a = client.post("/api/v1/urls", json=payload_a)
    resp_b = client.post("/api/v1/urls", json=payload_b)

    statuses = {resp_a.status_code, resp_b.status_code}
    assert statuses == {201, 409}

    # And the data reflects whichever one actually won -- not a mix of both.
    winner = resp_a if resp_a.status_code == 201 else resp_b
    metadata = client.get(f"/api/v1/urls/{winner.json()['short_code']}")
    assert metadata.json()["long_url"] == winner.json()["long_url"]
