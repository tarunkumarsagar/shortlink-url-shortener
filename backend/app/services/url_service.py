"""
URL service — business logic layer.

WHY a service layer separate from the API layer: the API layer
(FastAPI route handlers) should only handle HTTP concerns (parsing
requests, returning status codes). Business rules — "retry on
collision", "reject expired URLs on lookup", "validate the destination
URL" — belong here so they're testable without spinning up HTTP at all
(see tests/test_url_service.py) and reusable if we ever add a second
entry point (e.g., a CLI or gRPC interface) that isn't HTTP.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol
from urllib.parse import urlparse

from app.core.code_generator import generate_short_code
from app.repositories.exceptions import ShortCodeCollisionError

MAX_GENERATION_ATTEMPTS = 5
CACHE_KEY_PREFIX = "url:"


@dataclass
class ResolvedUrl:
    """
    Deliberately narrower than the repository's UrlRecord -- only the
    fields the consumers of resolve() actually need. This is exactly
    what we cache in Redis. Notably excludes owner_id: the ownership
    check in delete() reads from the repository directly, never
    through this cached, narrower view, keeping that security-sensitive
    check entirely outside the cache's blast radius.

    `id` IS included (added in Phase 5) -- it's the internal integer
    PK needed to record a click_events row via its url_id foreign key.
    Unlike owner_id, id carries no authorization meaning on its own
    (knowing a URL's internal id grants no access to anything), so
    including it in the cached payload doesn't reintroduce the risk we
    were guarding against by excluding owner_id.
    """

    short_code: str
    long_url: str
    created_at: datetime
    expires_at: Optional[datetime]
    id: Optional[int] = None


class UrlRepository(Protocol):
    """
    Structural interface both InMemoryUrlRepository (Phase 1) and
    PostgresUrlRepository (Phase 2) satisfy. Using a Protocol instead
    of importing a concrete repository class here is what makes this
    service layer genuinely storage-agnostic -- it depends on a shape,
    not an implementation, which is the actual point of the Repository
    Pattern (a concrete import here would have silently defeated it).
    """

    def exists(self, short_code: str) -> bool: ...
    def insert(
        self,
        short_code: str,
        long_url: str,
        expires_at: Optional[datetime] = None,
        owner_id: Optional[int] = None,
    ): ...
    def get(self, short_code: str): ...
    def delete(self, short_code: str) -> bool: ...


class Cache(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    def delete(self, key: str) -> None: ...


class InvalidUrlError(Exception):
    pass


class AliasAlreadyTakenError(Exception):
    pass


class UrlNotFoundError(Exception):
    pass


class UrlExpiredError(Exception):
    pass


class NotUrlOwnerError(Exception):
    """
    Raised when an authenticated user attempts to delete a URL they
    don't own. Mapped to 403 Forbidden at the API layer (403, not 404
    -- the resource DOES exist, the requester is just not permitted to
    act on it; a 404 here would be actively misleading about why the
    request failed).
    """


def _validate_long_url(long_url: str) -> None:
    """
    Minimal but real validation: must be http/https and have a network
    location. This also happens to be part of our security posture —
    see docs/decisions security notes on open redirects — full
    malicious-URL screening comes later; this just rejects obviously
    malformed input early.
    """
    parsed = urlparse(long_url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidUrlError("URL must start with http:// or https://")
    if not parsed.netloc:
        raise InvalidUrlError("URL must include a valid domain")


def _serialize(record) -> str:
    return json.dumps(
        {
            "id": record.id,
            "short_code": record.short_code,
            "long_url": record.long_url,
            "created_at": record.created_at.isoformat(),
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        }
    )


def _deserialize(raw: str) -> ResolvedUrl:
    data = json.loads(raw)
    return ResolvedUrl(
        id=data.get("id"),
        short_code=data["short_code"],
        long_url=data["long_url"],
        created_at=datetime.fromisoformat(data["created_at"]),
        expires_at=datetime.fromisoformat(data["expires_at"]) if data["expires_at"] else None,
    )


class UrlService:
    def __init__(
        self,
        repository: UrlRepository,
        cache: Optional[Cache] = None,
        cache_ttl_seconds: int = 300,
    ):
        self._repository = repository
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    def create_short_url(
        self,
        long_url: str,
        custom_alias: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        owner_id: Optional[int] = None,
    ):
        _validate_long_url(long_url)

        if custom_alias:
            # Custom alias path: no retry loop, because a collision here
            # is a legitimate user-facing error ("alias taken"), not a
            # transient generation issue to silently retry past.
            if self._repository.exists(custom_alias):
                raise AliasAlreadyTakenError(f"Alias '{custom_alias}' is already taken")
            return self._repository.insert(custom_alias, long_url, expires_at, owner_id)

        # Random-generation path: retry on the rare collision.
        for _ in range(MAX_GENERATION_ATTEMPTS):
            code = generate_short_code()
            try:
                return self._repository.insert(code, long_url, expires_at, owner_id)
            except ShortCodeCollisionError:
                continue  # regenerate and try again

        # If we get here, something is very wrong (either terrible luck
        # at a scale where our probability math says this shouldn't
        # happen, or the address space is nearly exhausted) — we fail
        # loudly rather than silently looping forever.
        raise RuntimeError(
            f"Failed to generate a unique short code after {MAX_GENERATION_ATTEMPTS} attempts"
        )

    def resolve(self, short_code: str) -> ResolvedUrl:
        """
        Cache-aside read. On a cache hit we still re-check expiry
        below -- the TTL we set on write is capped to expires_at, so
        this is a defensive double-check (e.g. against clock skew or
        a manually-set very-long TTL), not the primary enforcement
        mechanism.
        """
        cache_key = CACHE_KEY_PREFIX + short_code

        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                resolved = _deserialize(cached)
                self._raise_if_expired(short_code, resolved.expires_at)
                return resolved

        record = self._repository.get(short_code)
        if record is None:
            raise UrlNotFoundError(f"No URL found for code '{short_code}'")

        self._raise_if_expired(short_code, record.expires_at)

        resolved = ResolvedUrl(
            id=record.id,
            short_code=record.short_code,
            long_url=record.long_url,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )

        if self._cache is not None:
            ttl = self._cache_ttl_seconds
            if resolved.expires_at is not None:
                seconds_until_expiry = (
                    resolved.expires_at - datetime.now(timezone.utc)
                ).total_seconds()
                ttl = min(ttl, int(seconds_until_expiry))
            self._cache.set(cache_key, _serialize(resolved), ttl)

        return resolved

    @staticmethod
    def _raise_if_expired(short_code: str, expires_at: Optional[datetime]) -> None:
        if expires_at is not None and expires_at < datetime.now(timezone.utc):
            raise UrlExpiredError(f"URL for code '{short_code}' has expired")

    def delete(self, short_code: str, requesting_user_id: Optional[int] = None) -> bool:
        """
        Ownership rule: a URL with an owner can ONLY be deleted by that
        owner. An anonymously-created URL (owner_id is None) can still
        be deleted without authentication -- preserving the original
        MVP behavior for anonymous links, since there's no owner to
        protect. This check happens HERE, in the service layer, not
        only in the API layer, so it can't be bypassed by any future
        second entry point (CLI, gRPC, etc.) that calls this service
        directly.

        Note this reads from self._repository directly, NOT through
        the cache -- the ownership check must always see the current
        source of truth, never a cached (and owner_id-stripped) view.
        """
        record = self._repository.get(short_code)
        if record is None:
            return False

        if record.owner_id is not None and record.owner_id != requesting_user_id:
            raise NotUrlOwnerError(
                f"User {requesting_user_id} does not own short code '{short_code}'"
            )

        deleted = self._repository.delete(short_code)

        # MANDATORY invalidation -- see Phase 3/4 checkpoint discussion.
        # Deleting from Postgres without this leaves a fully-functional
        # stale entry in the cache for up to cache_ttl_seconds, which
        # is exactly the correctness bug we designed against.
        if deleted and self._cache is not None:
            self._cache.delete(CACHE_KEY_PREFIX + short_code)

        return deleted
