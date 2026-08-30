"""
Analytics endpoints. Basic, MVP-scope: total click count and recent
click events for a URL the requester owns. Deeper aggregation (clicks
over time buckets, geographic/device breakdowns as charts) is real,
valuable future work once there's enough real traffic to make
aggregation queries interesting -- built here just enough to prove the
whole pipeline (redirect -> queue -> worker -> Postgres) is actually
queryable end-to-end, which is the point of this phase.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import _pool, get_current_user
from app.repositories.click_event_repository import PostgresClickEventRepository
from app.repositories.postgres_url_repository import PostgresUrlRepository
from app.repositories.user_repository import UserRecord

router = APIRouter(prefix="/api/v1/urls", tags=["analytics"])


class ClickEventSummary(BaseModel):
    clicked_at: datetime
    referrer: Optional[str]
    device_type: Optional[str]
    browser: Optional[str]
    os: Optional[str]


class UrlAnalyticsResponse(BaseModel):
    short_code: str
    total_clicks: int
    recent_clicks: list[ClickEventSummary]


@router.get("/{short_code}/analytics", response_model=UrlAnalyticsResponse)
def get_url_analytics(short_code: str, current_user: UserRecord = Depends(get_current_user)):
    """
    Ownership-gated: only the URL's owner can see its analytics.
    Requires authentication (unlike creation, which supports
    anonymous use) -- there's no meaningful "anonymous owner" to check
    analytics for, so this endpoint has no anonymous path at all.
    """
    url_repository = PostgresUrlRepository(_pool)
    record = url_repository.get(short_code)
    if record is None:
        raise HTTPException(status_code=404, detail="Short code not found")

    if record.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this URL")

    click_repository = PostgresClickEventRepository(_pool)
    total = click_repository.get_count_for_url(record.id)
    recent = click_repository.get_recent_for_url(record.id, limit=20)

    return UrlAnalyticsResponse(
        short_code=short_code,
        total_clicks=total,
        recent_clicks=[
            ClickEventSummary(
                clicked_at=r.clicked_at,
                referrer=r.referrer,
                device_type=r.device_type,
                browser=r.browser,
                os=r.os,
            )
            for r in recent
        ],
    )
