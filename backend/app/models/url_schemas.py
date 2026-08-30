"""
Pydantic schemas — these define our API's request/response contract
and give us free validation (FastAPI rejects malformed requests with a
422 before our code even runs).
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CreateUrlRequest(BaseModel):
    long_url: str = Field(..., description="The destination URL to shorten")
    custom_alias: Optional[str] = Field(
        None, min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    expires_at: Optional[datetime] = None


class UrlResponse(BaseModel):
    short_code: str
    short_url: str
    long_url: str
    created_at: datetime
    expires_at: Optional[datetime] = None
