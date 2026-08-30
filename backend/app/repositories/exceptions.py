"""
Shared repository-layer exceptions.

Both InMemoryUrlRepository (Phase 1) and PostgresUrlRepository
(Phase 2) raise these same exception types. This is what lets
url_service.py depend on an abstraction rather than a specific
storage implementation -- it doesn't need to know or care whether
it's talking to a dict or a database, only that a collision raises
ShortCodeCollisionError.
"""


class ShortCodeCollisionError(Exception):
    """Raised when a short code already exists at insert time."""


class EmailAlreadyRegisteredError(Exception):
    """Raised when attempting to register an email that already exists."""
