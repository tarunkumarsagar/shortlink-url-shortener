"""
Security primitives: password hashing and JWT encoding/decoding.

Kept as pure functions in their own module (no FastAPI, no DB) so
they're trivially unit-testable and so the actual cryptographic
choices are easy to find and audit in one place.
"""

import datetime
from typing import Literal, TypedDict

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

# --- Password hashing -------------------------------------------------
#
# Argon2id (argon2-cffi's default) over bcrypt: OWASP's current
# recommended default, memory-hard (raises the cost of GPU/ASIC
# cracking specifically, which a purely CPU-slow algorithm like bcrypt
# does not). The PasswordHasher instance below uses argon2-cffi's
# sensible default cost parameters; tuning them is a legitimate future
# optimization once we can benchmark real login latency, not a Phase 3
# concern.

_password_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    """Returns a self-contained Argon2id hash string (includes salt
    and parameters) -- safe to store directly in users.password_hash."""
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        _password_hasher.verify(password_hash, plain_password)
        return True
    except VerifyMismatchError:
        return False


# --- JWT ----------------------------------------------------------------
#
# Access token: short-lived (30 min), sent on every authenticated
# request, verified statelessly (no DB lookup needed).
# Refresh token: longer-lived (7 days), used ONLY to mint new access
# tokens via /api/v1/auth/refresh -- never accepted by any other
# endpoint. This distinction is enforced by the "type" claim below,
# not just by which endpoint happens to receive it -- an access token
# presented to /refresh, or a refresh token presented as a Bearer
# token elsewhere, is explicitly rejected.

ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

TokenType = Literal["access", "refresh"]


class DecodedToken(TypedDict):
    sub: str  # user id, as a string (JWT spec convention for `sub`)
    type: TokenType
    exp: int


def _create_token(user_id: int, token_type: TokenType, expires_delta: datetime.timedelta) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def create_access_token(user_id: int) -> str:
    return _create_token(
        user_id, "access", datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )


def create_refresh_token(user_id: int) -> str:
    return _create_token(user_id, "refresh", datetime.timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))


class InvalidTokenError(Exception):
    pass


def decode_token(token: str, expected_type: TokenType) -> DecodedToken:
    """
    Decodes and validates a JWT, INCLUDING checking the `type` claim
    matches what the caller expects. This is what stops a refresh
    token from being usable as an access token (or vice versa) --
    without this check, either token would pass signature
    verification equally, since both are signed with the same key.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise InvalidTokenError("Token has expired")
    except jwt.InvalidTokenError:
        raise InvalidTokenError("Token is invalid")

    if payload.get("type") != expected_type:
        raise InvalidTokenError(f"Expected a {expected_type} token, got {payload.get('type')}")

    return payload  # type: ignore[return-value]
