-- Migration 002: create users table
--
-- Design rationale:
--   - email is the login identifier, not a separate username -- one
--     less unique-constraint surface to manage, and matches how most
--     users actually expect to log in.
--   - password_hash stores an Argon2id hash (via argon2-cffi), which
--     itself encodes the salt and cost parameters in the stored
--     string -- there is deliberately NO separate `salt` column,
--     because Argon2's hash format is self-contained. Adding one
--     would be redundant, unused schema.
--   - We do NOT store plaintext passwords anywhere, ever, including
--     in logs -- see backend/app/core/security.py for the hashing
--     implementation and app/services/auth_service.py for where
--     password values are handled and immediately discarded.
--   - is_active mirrors the same soft-delete pattern used in urls,
--     for the same reason: preserve referential integrity for a
--     user's historical urls/click_events rather than hard-deleting.

CREATE TABLE users (
    id             BIGSERIAL PRIMARY KEY,
    email          VARCHAR(255) NOT NULL,
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active      BOOLEAN NOT NULL DEFAULT true,

    CONSTRAINT users_email_unique UNIQUE (email)
);

COMMENT ON TABLE users IS 'Registered users. URL creation remains available to anonymous users (owner_id NULL on urls); this table only exists for those who register.';
COMMENT ON COLUMN users.password_hash IS 'Argon2id hash via argon2-cffi. Self-contained (includes salt + parameters) -- never store plaintext.';
