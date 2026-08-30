-- Migration 004: create click_events table
--
-- Design rationale:
--   - url_id references urls(id), the internal integer PK, not the
--     short_code string -- integer FK joins are cheaper, and this is
--     exactly why we chose BIGSERIAL for urls.id back in migration
--     001 even though short_code (not id) is what's user-facing.
--   - FK constraint kept for MVP correctness (guarantees no orphaned
--     click events), with the honest trade-off documented: every
--     insert here now pays an extra lookup against urls' PK index.
--     At genuinely high write volume this becomes a real cost -- the
--     actual fix for that is moving these writes off the synchronous
--     request path entirely (Phase 6's message queue), not dropping
--     the FK. See docs/decisions/ADR-006 (analytics architecture).
--   - clicked_at defaults to now() at the DATABASE level (not
--     application level) so we don't need to trust every write path
--     to set it correctly.
--   - PRIVACY: we deliberately do NOT store raw IP addresses or any
--     other directly-identifying data. No geoip lookup is implemented
--     in this phase (documented as a future, explicitly-optional
--     enhancement, likely via the free MaxMind GeoLite2 database) --
--     country is a nullable placeholder column for that future work,
--     not populated yet. user_agent is stored raw (browsers send this
--     to every site by default; it's not customer PII in the way an
--     IP or email is) alongside PARSED device/browser/os fields, which
--     is what the analytics dashboard will actually query against --
--     nobody queries "show me clicks with user_agent LIKE '%Chrome%'"
--     in production, they query the parsed column.
--   - referrer is nullable (many clients don't send one at all, e.g.
--     from a messaging app or a typed-in URL).

CREATE TABLE click_events (
    id           BIGSERIAL PRIMARY KEY,
    url_id       BIGINT NOT NULL REFERENCES urls(id),
    clicked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    referrer     TEXT NULL,
    user_agent   TEXT NULL,
    device_type  VARCHAR(20) NULL,   -- 'desktop' | 'mobile' | 'tablet' | 'bot' | 'unknown'
    browser      VARCHAR(50) NULL,
    os           VARCHAR(50) NULL,
    country      VARCHAR(2) NULL     -- ISO 3166-1 alpha-2; NOT populated yet, see above
);

-- Backs the two query patterns we already know the analytics
-- dashboard will need (Phase 13): "all clicks for URL X" and "clicks
-- for URL X over a time range" -- a composite index serves both,
-- since url_id is the leading, most-selective, always-present filter.
CREATE INDEX idx_click_events_url_id_clicked_at ON click_events (url_id, clicked_at);

COMMENT ON TABLE click_events IS 'One row per redirect. Written synchronously in Phase 5 (deliberately, to measure the cost) -- Phase 6 moves this off the request path via a message queue.';
COMMENT ON COLUMN click_events.country IS 'Reserved for future geoip enrichment. NULL in Phase 5 -- no geoip lookup implemented yet.';
