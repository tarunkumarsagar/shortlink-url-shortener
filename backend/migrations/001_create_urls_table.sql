-- Migration 001: create urls table
--
-- Design rationale is documented in detail in docs/decisions/ADR-002
-- (to be written) and in the Phase 2 conversation log. Summary:
--   - BIGSERIAL surrogate PK, not the short_code itself, so later
--     tables (click_events) can FK against a cheap integer.
--   - UNIQUE constraint on short_code is THE mechanism that makes
--     concurrent short-code generation safe (see repository comments)
--     -- this replaces the app-level check-then-insert race condition
--     that the in-memory Phase 1 repository was vulnerable to.
--   - owner_id is deliberately NOT included yet: no users table exists
--     until Phase 3. Adding it now would be a column with no code
--     using it. It arrives via migration 002 in Phase 3.
--   - is_active supports soft-delete, needed once click_events (Phase 5)
--     holds a foreign key into this table -- hard deletes would either
--     cascade-destroy analytics history or violate the FK constraint.

CREATE TABLE urls (
    id            BIGSERIAL PRIMARY KEY,
    short_code    VARCHAR(32) NOT NULL,
    long_url      TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NULL,
    is_active     BOOLEAN NOT NULL DEFAULT true,

    CONSTRAINT urls_short_code_unique UNIQUE (short_code)
);

-- The UNIQUE constraint above already creates a B-tree index on
-- short_code automatically -- this is not a redundant extra index,
-- just documenting that fact so it isn't "rediscovered" and
-- accidentally duplicated later.
--
-- COMMENT: this index is the single most important index in the
-- entire system -- it backs the redirect lookup, our hottest query.

COMMENT ON TABLE urls IS 'Core URL mapping table. One row per shortened URL.';
COMMENT ON COLUMN urls.is_active IS 'Soft-delete flag. false = deleted from the user''s perspective but retained for analytics FK integrity.';
