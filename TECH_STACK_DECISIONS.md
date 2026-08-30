# Tech Stack Decisions

This document explains every major technology choice in ShortLink and why it was selected. See docs/decisions/ for the full ADRs behind each of these -- this file is the condensed, interview-ready version.

## Stack Summary

| Layer | Technology | Why |
|---|---|---|
| Backend | Python + FastAPI | I/O-bound hot path, async-capable, Pydantic validation for free |
| Database | PostgreSQL | Relational integrity (FK, UNIQUE constraints) is the core correctness mechanism |
| Cache | Redis | Shared state across instances; sorted sets also back rate limiting |
| Message Queue | RabbitMQ | Ack/retry/DLQ semantics match real needs; Kafka's value isn't needed at this scale |
| Auth | JWT (access + refresh) + Argon2id | Stateless, horizontally-scalable; Argon2id is OWASP's current recommended hash |
| Containerization | Docker + Docker Compose | Free, reproducible, industry-standard local dev |
| CI | GitHub Actions | Free for public repos, real service containers for integration tests |

## PostgreSQL Q&A

Q1. Why PostgreSQL?
The data is relational by nature (users -> urls -> click_events), and the single most important correctness guarantee in the system -- safe concurrent short-code generation -- depends on an atomic UNIQUE constraint, a first-class relational feature.

Q2. Why not MongoDB?
MongoDB doesn't give atomic uniqueness enforcement across concurrent writes the way a relational UNIQUE constraint does, and the data has real foreign-key relationships (ownership, click attribution) that a document model would have to simulate at the application layer.

Q3. Why SQL for this project?
The queries are genuinely relational (joins between urls and click_events for analytics, ownership checks), and SQL/schema design fluency is itself a core SDE skill this project demonstrates.

Q4. Why do we need indexes?
Without an index, WHERE short_code = ? (the redirect's hottest query) is a full table scan. The UNIQUE constraint on short_code creates a B-tree index automatically -- the single most important index in the system.

Q5. Which columns did we index and why?
urls.short_code (unique, backs the redirect lookup), urls.owner_id (partial index, WHERE owner_id IS NOT NULL -- backs "show my URLs" without indexing majority-NULL anonymous rows), click_events(url_id, clicked_at) composite (backs "clicks for URL X over time"), click_events.event_id (unique, the idempotency mechanism).

Q6. What happens when the database becomes a bottleneck?
Read replicas for the read-heavy redirect/analytics paths; write-path bottlenecks are already mitigated by moving click_events writes off the synchronous request path entirely (Phase 6/7).

Q7. How would you scale PostgreSQL?
Vertically first, then read replicas, then partitioning click_events by time range once real query patterns are understood.

## Redis Q&A

Q1. Why Redis?
Shared, sub-millisecond state across multiple backend instances -- needed for both the redirect cache and the rate limiter, both requiring correctness under horizontal scaling.

Q2. Why not simply query PostgreSQL every time?
The redirect is the hottest, highest-volume read; hitting Postgres every time wastes connection-pool capacity on a workload that's overwhelmingly repeat reads of a small hot set.

Q3. What caching strategy did we use?
Cache-aside: check Redis first, on miss read Postgres and populate Redis, TTL capped by the URL's own expires_at.

Q4. What is cache-aside?
The application manages both the cache and source of truth explicitly -- cache misses trigger a read-through-and-populate, and the application owns invalidation.

Q5. What happens when Redis goes down?
Every Redis-touching path fails open: RedisCache catches connection errors and returns a miss/no-op, falling through to Postgres. Verified directly by stopping the Redis service mid-test-run and confirming redirects still succeeded fast.

Q6. How do we handle cache invalidation?
Explicitly, on delete: UrlService.delete() calls cache.delete() after a successful DB delete. Proven by direct Redis inspection in tests and a full manual walkthrough.

Q7. What happens when cached data becomes stale?
Bounded by TTL (5 minutes default, capped by the URL's own expiry).

Q8. How could Redis support distributed rate limiting?
Its atomic operations (sorted-set ZADD/ZCARD/ZREMRANGEBYSCORE) let multiple backend instances share one consistent view of request counts per identity.

## Message Queue (RabbitMQ) Q&A

Q1. Why did we need a message queue?
To decouple the redirect (must be fast, must not depend on analytics infra) from analytics processing (can be delayed, needs to be reliable).

Q2. Why not process analytics synchronously?
Measured it directly (Phase 5): a synchronous DB write added real latency to every redirect and coupled redirect availability to Postgres's availability.

Q3. Why RabbitMQ?
Its ack/retry/DLQ semantics map directly onto this project's reliability requirements without Kafka's operational overhead.

Q4. Why not Kafka?
Kafka's value (partitioned replay, very high throughput, independent consumer groups) isn't a real need at this scale. The concrete scenario that would justify it: needing to reprocess historical click data through a new consumer.

Q5. What is asynchronous processing?
Work that doesn't block the caller's response -- the caller schedules it and moves on, with a separate process handling it independently.

Q6. What happens when the consumer crashes?
If it crashes before acking, RabbitMQ redelivers the message once it detects the connection dropped.

Q7. What happens if a message is delivered twice?
The idempotent insert (INSERT ... ON CONFLICT (event_id) DO NOTHING) makes redelivery a safe no-op -- verified with 10 simulated redeliveries producing exactly one row.

Q8. How do we achieve idempotent processing?
A UUID (event_id) generated once at publish time, carried through any redelivery, enforced unique at the database level.

Q9. What is eventual consistency?
A guarantee that all parts of the system reflect an update eventually, not necessarily instantly.

Q10. How would you scale the consumers?
Run multiple worker instances; RabbitMQ naturally load-balances messages across them, aided by prefetch_count so one slow worker doesn't hoard the queue.

## Docker Q&A

Q1. Why Docker?
Reproducible environments -- the runtime is defined in code and identical everywhere.

Q2. What problem does containerization solve?
Dependency and environment drift between development, CI, and production.

Q3. Why Docker Compose?
Orchestrates the full local stack with one command and defined startup order (depends_on + healthchecks).

Q4. Image vs container?
An image is the built, immutable template; a container is a running instance of that image.

Q5. Why shouldn't credentials be in the image?
Images are often shared/pushed to registries and easy to inspect layer-by-layer. Credentials are injected at runtime via environment variables instead.

## API/REST Q&A

Q1. Why REST?
Well-understood, resource-oriented, maps naturally onto this domain.

Q2. What makes an API RESTful?
Resource-based URLs, standard HTTP methods carrying conventional meaning, statelessness, status codes communicating outcomes.

Q3. Why /api/v1?
Explicit versioning -- a future breaking change can ship as /api/v2 without breaking existing consumers.

Q4. Status codes used and why?
201 created, 200 success, 204 deleted, 302 redirect (not 301, so destination changes aren't permanently browser-cached), 400 bad input, 401 auth required/invalid, 403 authenticated but not permitted, 404 not found, 409 conflict, 410 gone (expired), 422 validation failure, 429 rate limited.

Q5. What is idempotency?
An operation producing the same result no matter how many times it's repeated. DELETE is idempotent; POST is not -- exactly why the click pipeline needed its own idempotency mechanism (event_id).

Q6. Which endpoints are idempotent?
GET, DELETE. POST (create) is not.

Q7. How do we validate API requests?
Pydantic schemas at the boundary (shape/type, free 422s) plus explicit business-rule validation in the service layer.

Q8. How do we secure the APIs?
JWT bearer auth, ownership checks in the service layer (not just the API layer), rate limiting on the highest-risk write path.
