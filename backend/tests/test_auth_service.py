from datetime import datetime, timezone

import pytest

from app.core.security import hash_password
from app.repositories.exceptions import EmailAlreadyRegisteredError
from app.repositories.user_repository import UserRecord
from app.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
    InvalidEmailError,
    WeakPasswordError,
)


class FakeUserRepository:
    """
    A minimal in-memory double satisfying AuthService's UserRepository
    Protocol -- same philosophy as InMemoryUrlRepository: keeps these
    tests fast and independent of Postgres, while the real DB-level
    guarantees (the UNIQUE constraint on email) are tested honestly
    against real Postgres in test_auth_api.py instead.
    """

    def __init__(self):
        self._by_email: dict[str, UserRecord] = {}
        self._next_id = 1

    def insert(self, email: str, password_hash: str) -> UserRecord:
        if email in self._by_email:
            raise EmailAlreadyRegisteredError(f"Email '{email}' is already registered")
        record = UserRecord(
            id=self._next_id,
            email=email,
            password_hash=password_hash,
            created_at=datetime.now(timezone.utc),
            is_active=True,
        )
        self._by_email[email] = record
        self._next_id += 1
        return record

    def get_by_email(self, email: str):
        return self._by_email.get(email)

    def get_by_id(self, user_id: int):
        for record in self._by_email.values():
            if record.id == user_id:
                return record
        return None


@pytest.fixture
def service():
    return AuthService(FakeUserRepository())


def test_register_creates_user(service):
    user = service.register("alice@example.com", "correct-horse-battery")
    assert user.email == "alice@example.com"
    assert user.password_hash != "correct-horse-battery"  # never store plaintext


def test_register_rejects_invalid_email(service):
    with pytest.raises(InvalidEmailError):
        service.register("not-an-email", "correct-horse-battery")


def test_register_rejects_short_password(service):
    with pytest.raises(WeakPasswordError):
        service.register("alice@example.com", "short")


def test_register_duplicate_email_raises(service):
    service.register("alice@example.com", "correct-horse-battery")
    with pytest.raises(EmailAlreadyRegisteredError):
        service.register("alice@example.com", "another-password")


def test_login_with_correct_credentials_succeeds(service):
    service.register("alice@example.com", "correct-horse-battery")
    access_token, refresh_token = service.login("alice@example.com", "correct-horse-battery")
    assert access_token
    assert refresh_token
    assert access_token != refresh_token


def test_login_with_wrong_password_raises(service):
    service.register("alice@example.com", "correct-horse-battery")
    with pytest.raises(InvalidCredentialsError):
        service.login("alice@example.com", "wrong-password")


def test_login_with_unknown_email_raises_same_error_as_wrong_password(service):
    """
    Directly verifies the anti-enumeration property: unknown email and
    wrong password must raise the exact same exception type, so the
    API layer cannot accidentally expose different responses for each.
    """
    with pytest.raises(InvalidCredentialsError):
        service.login("nobody@example.com", "whatever-password")


def test_refresh_with_valid_refresh_token_issues_new_access_token(service):
    service.register("alice@example.com", "correct-horse-battery")
    _, refresh_token = service.login("alice@example.com", "correct-horse-battery")

    new_access_token = service.refresh(refresh_token)
    assert new_access_token


def test_refresh_with_access_token_instead_of_refresh_token_fails(service):
    """The type-claim check from core/security.py, exercised through
    the full service layer this time."""
    service.register("alice@example.com", "correct-horse-battery")
    access_token, _ = service.login("alice@example.com", "correct-horse-battery")

    with pytest.raises(InvalidCredentialsError):
        service.refresh(access_token)


def test_refresh_with_garbage_token_fails(service):
    with pytest.raises(InvalidCredentialsError):
        service.refresh("not-a-real-token")
