from datetime import datetime, timedelta, timezone

import pytest

from app.repositories.url_repository import InMemoryUrlRepository
from app.services.url_service import (
    AliasAlreadyTakenError,
    InvalidUrlError,
    UrlExpiredError,
    UrlNotFoundError,
    UrlService,
)


@pytest.fixture
def service():
    """Fresh repository + service per test -- tests must not leak
    state into each other."""
    return UrlService(InMemoryUrlRepository())


def test_create_and_resolve_round_trip(service):
    record = service.create_short_url("https://example.com/page")
    resolved = service.resolve(record.short_code)
    assert resolved.long_url == "https://example.com/page"


def test_create_with_custom_alias(service):
    record = service.create_short_url("https://example.com", custom_alias="my-brand")
    assert record.short_code == "my-brand"


def test_duplicate_custom_alias_raises(service):
    service.create_short_url("https://example.com", custom_alias="taken")
    with pytest.raises(AliasAlreadyTakenError):
        service.create_short_url("https://other.com", custom_alias="taken")


def test_rejects_url_without_scheme(service):
    with pytest.raises(InvalidUrlError):
        service.create_short_url("example.com")  # missing http(s)://


def test_rejects_non_http_scheme(service):
    with pytest.raises(InvalidUrlError):
        service.create_short_url("ftp://example.com")


def test_resolve_unknown_code_raises(service):
    with pytest.raises(UrlNotFoundError):
        service.resolve("nonexistent")


def test_resolve_expired_url_raises(service):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    record = service.create_short_url("https://example.com", expires_at=past)
    with pytest.raises(UrlExpiredError):
        service.resolve(record.short_code)


def test_resolve_not_yet_expired_url_succeeds(service):
    future = datetime.now(timezone.utc) + timedelta(days=1)
    record = service.create_short_url("https://example.com", expires_at=future)
    resolved = service.resolve(record.short_code)  # should not raise
    assert resolved.long_url == "https://example.com"


def test_delete_removes_url(service):
    record = service.create_short_url("https://example.com")
    assert service.delete(record.short_code) is True
    with pytest.raises(UrlNotFoundError):
        service.resolve(record.short_code)


def test_delete_nonexistent_returns_false(service):
    assert service.delete("nonexistent") is False


def test_generated_short_codes_are_unique_across_many_creates(service):
    """Not a proof of the birthday-bound math, but a real smoke test
    that the retry-on-collision path at least produces distinct codes
    under normal operation."""
    codes = {service.create_short_url("https://example.com").short_code for _ in range(200)}
    assert len(codes) == 200
