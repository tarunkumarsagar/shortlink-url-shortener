"""
URL repository — Repository Pattern.

WHY this pattern, here, now (not forced): the service layer (url_service.py)
should not know or care whether URLs live in a Python dict, Postgres, or
anything else. By defining this interface now, swapping in a real
PostgreSQL-backed repository in Phase 2 requires ZERO changes to the
service layer or API layer — only a new class that satisfies the same
methods. That's the concrete payoff of the pattern, not just "best
practice" for its own sake.

CONCURRENCY NOTE (read this before Phase 2):
    In Phase 2, "check if code exists, then insert" becomes a genuine
    race condition across concurrent requests/processes, because two
    requests could both pass the "does it exist?" check before either
    has inserted. The FIX there is a database-level UNIQUE constraint
    combined with catching the resulting IntegrityError — NOT an
    application-level lock. We are deliberately writing this in-memory
    version to already reflect that shape (check + insert, with a
    retry loop on conflict) so the migration in Phase 2 is a small,
    well-understood diff rather than a redesign.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.repositories.exceptions import ShortCodeCollisionError

__all__ = ["ShortCodeCollisionError", "UrlRecord", "InMemoryUrlRepository"]


@dataclass
class UrlRecord:
    short_code: str
    long_url: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    owner_id: Optional[int] = None
    id: Optional[int] = None


class InMemoryUrlRepository:
    """
    Phase 1 implementation. Not thread-safe beyond Python's GIL-level
    dict-operation atomicity — that's fine for a single-process demo,
    and we call this limitation out explicitly rather than pretending
    it's production-grade.
    """

    def __init__(self) -> None:
        self._store: dict[str, UrlRecord] = {}
        self._next_id = 1

    def exists(self, short_code: str) -> bool:
        return short_code in self._store

    def insert(
        self,
        short_code: str,
        long_url: str,
        expires_at: Optional[datetime] = None,
        owner_id: Optional[int] = None,
    ) -> UrlRecord:
        if self.exists(short_code):
            raise ShortCodeCollisionError(f"Short code '{short_code}' already exists")
        record = UrlRecord(
            short_code=short_code,
            long_url=long_url,
            created_at=datetime.now(timezone.utc),
            expires_at=expires_at,
            owner_id=owner_id,
            id=self._next_id,
        )
        self._next_id += 1
        self._store[short_code] = record
        return record

    def get(self, short_code: str) -> Optional[UrlRecord]:
        return self._store.get(short_code)

    def delete(self, short_code: str) -> bool:
        if short_code in self._store:
            del self._store[short_code]
            return True
        return False
