"""
Auth service — business logic layer, same philosophy as UrlService:
HTTP-independent, unit-testable, owns the actual rules.
"""

import re
from typing import Protocol

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.repositories.exceptions import EmailAlreadyRegisteredError
from app.repositories.user_repository import UserRecord

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


class UserRepository(Protocol):
    def insert(self, email: str, password_hash: str) -> UserRecord: ...
    def get_by_email(self, email: str) -> UserRecord | None: ...
    def get_by_id(self, user_id: int) -> UserRecord | None: ...


class InvalidEmailError(Exception):
    pass


class WeakPasswordError(Exception):
    pass


class InvalidCredentialsError(Exception):
    """
    Deliberately the SAME exception for both 'email not found' and
    'wrong password'. Returning a different error for each ("no such
    user" vs "wrong password") is a classic user-enumeration
    vulnerability -- it lets an attacker discover which emails are
    registered by observing which error they get back. We trade a
    slightly less specific error message for closing that leak.
    """


class AuthService:
    def __init__(self, repository: UserRepository):
        self._repository = repository

    def register(self, email: str, password: str) -> UserRecord:
        if not EMAIL_PATTERN.match(email):
            raise InvalidEmailError("Invalid email format")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

        password_hash = hash_password(password)
        # EmailAlreadyRegisteredError propagates directly -- the
        # repository already raises exactly the exception type the
        # API layer expects to catch, no translation needed here.
        return self._repository.insert(email, password_hash)

    def login(self, email: str, password: str) -> tuple[str, str]:
        """Returns (access_token, refresh_token)."""
        user = self._repository.get_by_email(email)
        if user is None:
            raise InvalidCredentialsError("Invalid email or password")

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password")

        return create_access_token(user.id), create_refresh_token(user.id)

    def refresh(self, refresh_token: str) -> str:
        """Returns a new access_token. Does NOT rotate the refresh
        token in this version -- refresh token rotation (issuing a new
        refresh token on each use, invalidating the old one) is a
        real, worthwhile hardening step that requires tracking issued
        refresh tokens server-side (defeating pure statelessness) --
        documented as a future improvement once Redis exists (Phase 4)
        to back that tracking cheaply."""
        from app.core.security import InvalidTokenError

        try:
            decoded = decode_token(refresh_token, expected_type="refresh")
        except InvalidTokenError:
            raise InvalidCredentialsError("Invalid or expired refresh token")

        user_id = int(decoded["sub"])
        user = self._repository.get_by_id(user_id)
        if user is None:
            raise InvalidCredentialsError("User no longer exists")

        return create_access_token(user.id)
