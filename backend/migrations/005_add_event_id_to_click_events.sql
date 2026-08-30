-- Migration 005: add event_id to click_events
--
-- This is THE mechanism behind idempotent processing, discussed at
-- length in the Phase 6/7 checkpoint: RabbitMQ's at-least-once
-- delivery guarantee means the Phase 7 worker WILL occasionally
-- receive the same message twice (e.g. it crashes after writing to
-- Postgres but before acking). Without a way to recognize "I've
-- already processed this exact event," a redelivery becomes a
-- duplicate row.
--
-- event_id is a UUID generated once, at publish time (see
-- core/message_queue.py's build_click_event_message), carried
-- unchanged through any redelivery. The worker performs
-- INSERT ... ON CONFLICT (event_id) DO NOTHING -- a redelivered
-- message becomes a harmless no-op insert instead of a duplicate row.
--
-- NULLABLE, not NOT NULL: rows written before this migration (Phase 5's
-- synchronous-write era, and any manual testing since) have no
-- event_id. Postgres's UNIQUE constraint permits multiple NULLs (NULL
-- is never considered equal to NULL for uniqueness purposes), so this
-- is safe to add without a backfill -- new rows from the Phase 7
-- worker onward will always populate it.

ALTER TABLE click_events
    ADD COLUMN event_id UUID NULL;

CREATE UNIQUE INDEX idx_click_events_event_id_unique ON click_events (event_id);

COMMENT ON COLUMN click_events.event_id IS 'UUID from the published message. Enables idempotent INSERT ... ON CONFLICT DO NOTHING against RabbitMQ redelivery. NULL for pre-Phase-7 rows only.';
