# ADR-006: Analytics Architecture (Sync -> Async Evolution)

## Context
Every redirect should record a click event (device, browser, OS, referrer) without slowing down or endangering the redirect itself.

## Evolution (built deliberately in this order, not skipped)

### Phase 5: Synchronous write (intentionally, to measure the cost)
The redirect endpoint parsed the User-Agent and wrote directly to click_events inline, before responding. Measured cost on this machine: +0.71ms mean / +0.96ms p99 per redirect versus a no-op baseline (TestClient, single-threaded, local Postgres -- explicitly not a load-test claim, a relative-cost sanity check). More importantly: any Postgres slowness or outage would have directly degraded or broken the redirect, the one thing this whole system exists to do.

### Phase 6: Asynchronous publish
The redirect now builds a small JSON message (event_id, url_id, raw user_agent, referrer, occurred_at) and schedules its publish via FastAPI's BackgroundTasks -- which Starlette runs AFTER the HTTP response is sent. User-Agent parsing was also moved off the hot path entirely (CPU-bound work, not just I/O -- leaving it on the request path while only moving the DB write would have been a half-measure).

### Phase 7: Reliable, idempotent consumption
A separate worker process consumes the queue with manual acknowledgment (ack only after the DB write commits -- required for at-least-once delivery; auto-ack would silently lose events on a worker crash between receive and write). Idempotency via click_events.event_id UNIQUE constraint + INSERT ... ON CONFLICT DO NOTHING absorbs RabbitMQ's at-least-once redelivery guarantee without producing duplicate rows -- verified with 10 simulated redeliveries of the same event producing exactly one row. Malformed messages route immediately to a dead-letter queue; transient failures get bounded retries before also dead-lettering.

## Decision
Fully asynchronous, event-driven analytics pipeline: Redirect -> publish (fire-and-forget, post-response) -> RabbitMQ -> Worker (idempotent, manually-acked consumption) -> Postgres.

## Reason
This is the direct payoff of moving through Phase 5 first rather than jumping straight to the "right" architecture: the synchronous version's cost was measured, not assumed, which makes the async version's justification an engineering decision backed by evidence rather than a diagram copied from a tutorial.

## Trade-offs
- The publish step itself is still a narrow best-effort window: if RabbitMQ is unreachable at the exact moment of publish, that one event is lost (logged, not silently swallowed). Strictly better than Phase 5 (where any failure anywhere in the write path lost the event), not a claim of zero data loss.
- Analytics data is eventually consistent -- a click may take a moment to appear in the analytics endpoint after the redirect completes. Accepted, correct trade-off: analytics don't need to be as fresh as the redirect itself needs to be fast.

## Future
A geoip lookup for country-level analytics is a reserved, unpopulated column (click_events.country) -- deferred because it needs a real geoip data source (e.g. MaxMind GeoLite2) and wasn't essential to prove the pipeline architecture.
