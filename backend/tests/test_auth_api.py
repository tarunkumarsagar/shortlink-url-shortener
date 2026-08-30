"""
End-to-end auth + ownership tests against the REAL FastAPI app and
REAL Postgres (via TestClient, no dependency overrides). This
deliberately does NOT mock anything -- it's the only place in the
suite proving the full path: HTTP -> auth routes -> AuthService ->
PostgresUserRepository -> real DB, AND separately, that URL ownership
is actually enforced end-to-end once real JWTs are involved.

Requires a running Postgres with migrations applied (same requirement
as test_postgres_repository.py).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


def register_and_login(client, email: str, password: str = "correct-horse-battery"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"], r.json()["refresh_token"]


# --- Registration / login / refresh, full HTTP round trip ---------------------------------------------------

def test_register_login_refresh_full_flow(client):
    email = unique_email()

    register_resp = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "correct-horse-battery"}
    )
    assert register_resp.status_code == 201
    assert register_resp.json()["email"] == email

    login_resp = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery"}
    )
    assert login_resp.status_code == 200
    tokens = login_resp.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    refresh_resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh_resp.status_code == 200
    assert "access_token" in refresh_resp.json()


def test_duplicate_registration_returns_409(client):
    email = unique_email()
    client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-battery"})
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "another-password"})
    assert r.status_code == 409


def test_login_wrong_password_returns_401(client):
    email = unique_email()
    client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-battery"})
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password"})
    assert r.status_code == 401


def test_login_unknown_email_returns_401_not_404(client):
    """Confirms the anti-enumeration behavior all the way through HTTP,
    not just at the service layer."""
    r = client.post(
        "/api/v1/auth/login", json={"email": unique_email(), "password": "whatever"}
    )
    assert r.status_code == 401


def test_refresh_with_access_token_is_rejected(client):
    email = unique_email()
    access_token, _ = register_and_login(client, email)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
    assert r.status_code == 401


# --- URL ownership enforcement, real JWTs, two real users ---------------------------------------------------

def test_authenticated_create_attaches_ownership_and_owner_can_delete(client):
    email = unique_email()
    access_token, _ = register_and_login(client, email)

    create_resp = client.post(
        "/api/v1/urls",
        json={"long_url": "https://example.com/owned"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert create_resp.status_code == 201
    code = create_resp.json()["short_code"]

    delete_resp = client.delete(
        f"/api/v1/urls/{code}", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert delete_resp.status_code == 204


def test_non_owner_cannot_delete_someone_elses_url(client):
    """
    THE core ownership test: two genuinely different, separately
    registered users, with their own real JWTs issued by the real
    login flow. User B must NOT be able to delete User A's URL.
    """
    owner_token, _ = register_and_login(client, unique_email())
    other_token, _ = register_and_login(client, unique_email())

    create_resp = client.post(
        "/api/v1/urls",
        json={"long_url": "https://example.com/owned-by-a"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    code = create_resp.json()["short_code"]

    forbidden_resp = client.delete(
        f"/api/v1/urls/{code}", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert forbidden_resp.status_code == 403

    # And prove it's genuinely still there -- not silently deleted despite the 403.
    still_exists_resp = client.get(f"/api/v1/urls/{code}")
    assert still_exists_resp.status_code == 200


def test_anonymous_can_still_delete_anonymous_url(client):
    """Preserves original MVP behavior: URLs created without auth have
    no owner, so anyone can delete them (no owner to protect)."""
    create_resp = client.post("/api/v1/urls", json={"long_url": "https://example.com/anon"})
    code = create_resp.json()["short_code"]

    delete_resp = client.delete(f"/api/v1/urls/{code}")
    assert delete_resp.status_code == 204


def test_anonymous_cannot_delete_owned_url(client):
    """The other direction: an anonymous request must not be able to
    delete a URL that DOES have an owner."""
    access_token, _ = register_and_login(client, unique_email())

    create_resp = client.post(
        "/api/v1/urls",
        json={"long_url": "https://example.com/protected"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    code = create_resp.json()["short_code"]

    delete_resp = client.delete(f"/api/v1/urls/{code}")  # no auth header
    assert delete_resp.status_code == 403


def test_invalid_bearer_token_on_create_returns_401_not_treated_as_anonymous(client):
    """
    A provided-but-invalid token must be rejected outright, not
    silently downgraded to "anonymous request" -- otherwise an
    expired/tampered token would fail open into anonymous access
    instead of failing closed with a clear error.
    """
    r = client.post(
        "/api/v1/urls",
        json={"long_url": "https://example.com"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401
