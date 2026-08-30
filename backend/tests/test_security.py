import time

import pytest

from app.core.security import (
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


# --- Password hashing ---------------------------------------------------

def test_hash_password_does_not_return_plaintext():
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"


def test_verify_correct_password_succeeds():
    hashed = hash_password("my-secret-password")
    assert verify_password("my-secret-password", hashed) is True


def test_verify_incorrect_password_fails():
    hashed = hash_password("my-secret-password")
    assert verify_password("wrong-password", hashed) is False


def test_same_password_produces_different_hashes():
    """Critical property: Argon2 salts automatically, so hashing the
    same password twice must NOT produce identical output -- otherwise
    two users with the same password would be visibly identifiable in
    a database leak, and rainbow-table-style precomputation would work
    again."""
    hash_a = hash_password("shared-password")
    hash_b = hash_password("shared-password")
    assert hash_a != hash_b
    # but both still verify correctly against the same plaintext
    assert verify_password("shared-password", hash_a) is True
    assert verify_password("shared-password", hash_b) is True


# --- JWT: access tokens ---------------------------------------------------

def test_access_token_round_trips_user_id():
    token = create_access_token(user_id=42)
    decoded = decode_token(token, expected_type="access")
    assert decoded["sub"] == "42"


def test_access_token_rejected_when_expected_type_is_refresh():
    """This is the specific check that stops an access token from
    being replayed against the /refresh endpoint."""
    token = create_access_token(user_id=1)
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="refresh")


# --- JWT: refresh tokens ---------------------------------------------------

def test_refresh_token_round_trips_user_id():
    token = create_refresh_token(user_id=99)
    decoded = decode_token(token, expected_type="refresh")
    assert decoded["sub"] == "99"


def test_refresh_token_rejected_when_expected_type_is_access():
    """The symmetric case: a refresh token must not work as an access
    token, even though both are signed with the same secret key."""
    token = create_refresh_token(user_id=1)
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access")


# --- JWT: tampering / invalid input ---------------------------------------------------

def test_malformed_token_raises_invalid_token_error():
    with pytest.raises(InvalidTokenError):
        decode_token("not-a-real-jwt", expected_type="access")


def test_tampered_token_signature_is_rejected():
    """Flip a character in the signature portion of a real token and
    confirm it's rejected -- this is what actually proves the HMAC
    signature is being checked, not just the payload shape."""
    token = create_access_token(user_id=1)
    header, payload, signature = token.split(".")
    tampered_signature = ("a" if signature[0] != "a" else "b") + signature[1:]
    tampered_token = f"{header}.{payload}.{tampered_signature}"

    with pytest.raises(InvalidTokenError):
        decode_token(tampered_token, expected_type="access")
