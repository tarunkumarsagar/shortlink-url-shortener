"""
FastAPI dependency providers.

WHY dependency injection now, replacing Phase 1's module-level
singleton: that singleton was an explicit, called-out simplification
("TODO Phase 2/3 fix" -- see the old comment in api/urls.py). It
worked for an in-memory dict with no lifecycle to manage, but a real
Postgres connection pool DOES have a lifecycle (must be opened at
startup, closed at shutdown) and real tests need to substitute a test
pool/repository without monkeypatching module globals. FastAPI's
Depends() mechanism solves both cleanly and is the idiomatic pattern
for this framework.
"""

from typing import Iterator, Optional

import redis
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg_pool import ConnectionPool

from app.core.cache import RedisCache
from app.core.config import settings
from app.core.message_queue import RabbitMQPublisher
from app.core.rate_limiter import RateLimiter
from app.core.security import InvalidTokenError, decode_token
from app.repositories.postgres_url_repository import PostgresUrlRepository
from app.repositories.user_repository import PostgresUserRepository, UserRecord
from app.services.auth_service import AuthService
from app.services.click_event_service import ClickEventService
from app.services.url_service import UrlService

# One pool per process, opened at import time. FastAPI's lifespan
# events would be the more rigorous place to open/close this (tying
# it to app startup/shutdown explicitly) -- we'll move it there
# alongside main.py once we add the /ready health check in a later
# phase, since that check needs to reference this same pool.
_pool = ConnectionPool(settings.database_url, min_size=2, max_size=10, open=True)

# redis-py's client is itself connection-pooled internally and is
# documented as safe to share across threads -- one client per process,
# same lifecycle reasoning as the Postgres pool above.
# socket_connect_timeout / socket_timeout are set deliberately short:
# if Redis is unreachable, we want cache calls to fail FAST and fall
# through to Postgres (see core/cache.py's fail-open behavior), not
# hang the request waiting on a dead connection.
_redis_client = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)
_cache = RedisCache(_redis_client)


def get_url_service() -> Iterator[UrlService]:
    repository = PostgresUrlRepository(_pool)
    yield UrlService(repository, cache=_cache, cache_ttl_seconds=settings.redis_cache_ttl_seconds)


def get_click_event_service() -> Iterator[ClickEventService]:
    publisher = RabbitMQPublisher(settings.rabbitmq_url, settings.click_events_queue_name)
    yield ClickEventService(publisher)


_rate_limiter = RateLimiter(_redis_client)


def get_auth_service() -> Iterator[AuthService]:
    repository = PostgresUserRepository(_pool)
    yield AuthService(repository)


# auto_error=False so we can distinguish "no token provided" (fine for
# the OPTIONAL variant below, used by anonymous-friendly endpoints
# like URL creation) from "token provided but invalid" (always a 401,
# never silently ignored).
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> UserRecord:
    """
    REQUIRED auth. Use this dependency on endpoints that must reject
    unauthenticated requests outright (e.g. a future 'my profile'
    endpoint).
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        decoded = decode_token(credentials.credentials, expected_type="access")
    except InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))

    repository = PostgresUserRepository(_pool)
    user = repository.get_by_id(int(decoded["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")

    return user


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[UserRecord]:
    """
    OPTIONAL auth. Use this on endpoints that support BOTH anonymous
    and authenticated access -- e.g. URL creation (MVP requirement:
    anonymous users can shorten URLs; authenticated users additionally
    get ownership attached). Returns None for anonymous requests, but
    still raises 401 if a token WAS provided and is invalid -- an
    invalid token should never be silently treated as "no token".
    """
    if credentials is None:
        return None

    try:
        decoded = decode_token(credentials.credentials, expected_type="access")
    except InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))

    repository = PostgresUserRepository(_pool)
    return repository.get_by_id(int(decoded["sub"]))


def enforce_create_url_rate_limit(
    request: Request,
    current_user: Optional[UserRecord] = Depends(get_current_user_optional),
) -> None:
    """
    Authenticated users get a higher limit and are keyed by user id
    (stable identity, fair even behind shared IPs/NAT/corporate
    proxies). Anonymous requests are keyed by IP -- the best identity
    available without requiring login, with the known, accepted
    weakness that many users can share one IP (e.g. behind NAT) and
    will share one quota.
    """
    if current_user is not None:
        identity = f"user:{current_user.id}"
        limit = settings.rate_limit_per_minute_authenticated
    else:
        client_ip = request.client.host if request.client else "unknown"
        identity = f"ip:{client_ip}"
        limit = settings.rate_limit_per_minute_anonymous

    if not _rate_limiter.is_allowed(identity, limit):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please slow down.")
