"""
URL API routes.

Design notes on HTTP semantics (you'll be asked about these):
  - POST /api/v1/urls -> 201 Created (a new resource was created),
    with a Location-style body containing the short_code.
  - GET /{short_code}  -> 302 Found redirect. We use 302 (temporary),
    not 301 (permanent), deliberately: 301 tells browsers/CDNs "cache
    this redirect forever," which would make our future feature
    "update destination URL" invisible to anyone whose browser cached
    a 301. 302 keeps the destination lookup authoritative on OUR
    server, at the cost of the browser not caching it — a trade-off
    we accept because destination-mutability matters more to us than
    shaving a lookup.
  - Duplicate custom alias -> 409 Conflict (resource already exists,
    conflicts with the desired state).
  - Invalid long_url -> 422 handled automatically by Pydantic for
    shape issues; our own validation raises a 400 for semantic issues
    (e.g., syntactically valid JSON, semantically invalid URL).
  - Unknown short_code -> 404 Not Found.
  - Expired short_code -> 410 Gone (it existed, it's intentionally no
    longer available — a more precise signal than a generic 404).

Phase 2 change: the service is now obtained via FastAPI's Depends()
(see api/dependencies.py) instead of a module-level singleton. This
means each request gets a repository backed by a real pooled Postgres
connection, and tests can override get_url_service() to inject a test
double without touching module globals.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.dependencies import (
    enforce_create_url_rate_limit,
    get_click_event_service,
    get_current_user_optional,
    get_url_service,
)
from app.models.url_schemas import CreateUrlRequest, UrlResponse
from app.repositories.user_repository import UserRecord
from app.services.click_event_service import ClickEventService
from app.services.url_service import (
    AliasAlreadyTakenError,
    InvalidUrlError,
    NotUrlOwnerError,
    UrlExpiredError,
    UrlNotFoundError,
    UrlService,
)

router = APIRouter()


@router.post("/api/v1/urls", response_model=UrlResponse, status_code=201)
def create_short_url(
    payload: CreateUrlRequest,
    request: Request,
    service: UrlService = Depends(get_url_service),
    current_user: UserRecord | None = Depends(get_current_user_optional),
    _rate_limit: None = Depends(enforce_create_url_rate_limit),
) -> UrlResponse:
    try:
        record = service.create_short_url(
            long_url=payload.long_url,
            custom_alias=payload.custom_alias,
            expires_at=payload.expires_at,
            owner_id=current_user.id if current_user else None,
        )
    except InvalidUrlError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AliasAlreadyTakenError as e:
        raise HTTPException(status_code=409, detail=str(e))

    base_url = str(request.base_url).rstrip("/")
    return UrlResponse(
        short_code=record.short_code,
        short_url=f"{base_url}/{record.short_code}",
        long_url=record.long_url,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


@router.get("/api/v1/urls/{short_code}", response_model=UrlResponse)
def get_url_metadata(
    short_code: str,
    request: Request,
    service: UrlService = Depends(get_url_service),
) -> UrlResponse:
    """Fetch metadata WITHOUT redirecting -- used by the owner/dashboard,
    distinct from the public redirect endpoint below."""
    try:
        record = service.resolve(short_code)
    except UrlNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UrlExpiredError as e:
        raise HTTPException(status_code=410, detail=str(e))

    base_url = str(request.base_url).rstrip("/")
    return UrlResponse(
        short_code=record.short_code,
        short_url=f"{base_url}/{record.short_code}",
        long_url=record.long_url,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


@router.delete("/api/v1/urls/{short_code}", status_code=204)
def delete_url(
    short_code: str,
    service: UrlService = Depends(get_url_service),
    current_user: UserRecord | None = Depends(get_current_user_optional),
):
    try:
        deleted = service.delete(
            short_code, requesting_user_id=current_user.id if current_user else None
        )
    except NotUrlOwnerError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail="Short code not found")
    return None


@router.get("/{short_code}")
def redirect_to_long_url(
    short_code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    service: UrlService = Depends(get_url_service),
    click_service: ClickEventService = Depends(get_click_event_service),
):
    """
    The hot path. Phase 6 finally implements the "respond, then
    notify" pattern discussed all the way back in Phase 1's Q&A:
    click-event publishing is scheduled via FastAPI's BackgroundTasks,
    which Starlette runs AFTER the response has been sent to the
    client, not before. The user's redirect is no longer delayed by
    -- or coupled to the availability of -- the message broker at all.

    Compare this to Phase 5, where record_click() was called inline,
    blocking the response on a synchronous Postgres write. That
    version's measured cost (see Phase 5 README notes) is the direct,
    concrete justification for this change, not a diagram we copied.
    """
    try:
        record = service.resolve(short_code)
    except UrlNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UrlExpiredError as e:
        raise HTTPException(status_code=410, detail=str(e))

    background_tasks.add_task(
        click_service.record_click,
        url_id=record.id,
        user_agent_string=request.headers.get("user-agent"),
        referrer=request.headers.get("referer"),  # yes, "referer" -- the HTTP header's historic misspelling
    )

    return RedirectResponse(url=record.long_url, status_code=302)
