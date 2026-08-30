-- Migration 003: add owner_id to urls
--
-- Deferred from migration 001 deliberately (see that file's header
-- comment): adding this column only now, alongside the users table
-- that gives it meaning, rather than speculatively in Phase 2.
--
-- NULLABLE by design: anonymous URL creation is a real MVP
-- requirement (see Phase 0 functional requirements), so owner_id
-- being NULL is a valid, common, expected state -- not a data-quality
-- problem to "fix" later.
--
-- ON DELETE SET NULL: if a user account is ever hard-deleted (not
-- currently exposed via any endpoint, but the constraint should still
-- be correct), their previously-created URLs should keep working as
-- anonymous/orphaned links rather than being force-deleted or
-- blocking the user deletion entirely.

ALTER TABLE urls
    ADD COLUMN owner_id BIGINT NULL REFERENCES users(id) ON DELETE SET NULL;

-- Backs the dashboard's core query: "all URLs owned by user X".
-- Not needed until this column (and real authenticated traffic)
-- exists, which is why it wasn't created in migration 001.
CREATE INDEX idx_urls_owner_id ON urls (owner_id) WHERE owner_id IS NOT NULL;

COMMENT ON COLUMN urls.owner_id IS 'NULL = anonymously created URL. References users.id.';
