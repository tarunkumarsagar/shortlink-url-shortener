# System Design Q&A

## Architecture

Q: Explain the architecture of ShortLink.
Short: A FastAPI backend handles URL CRUD and auth, backed by PostgreSQL. Redirects check a Redis cache before falling back to Postgres. On redirect, a click event is published to RabbitMQ (after the response is sent) and consumed by a separate worker process that writes it to Postgres idempotently.

Detailed: Client -> FastAPI (auth/rate-limit checked) -> UrlService.resolve() checks Redis cache-aside, falls back to Postgres on miss, repopulates cache -> redirect response sent -> BackgroundTasks publishes a click event to RabbitMQ -> analytics worker (separate process) consumes with manual ack, parses the user-agent, writes idempotently to click_events (event_id UNIQUE constraint absorbs redelivery) -> ack sent only after the write commits.

Follow-up: What would you change for 10x traffic? Read replicas for Postgres, horizontal scaling of both the backend (stateless, already supports this) and the worker (RabbitMQ naturally load-balances across worker instances), possibly Redis Cluster if the cache became a bottleneck.

Q: Walk me through a request from the user to the database.
A redirect request hits GET /{short_code} (registered after all specific routes, since Starlette matches by registration order not specificity -- a real bug hit and fixed in Phase 1). UrlService.resolve() checks Redis via a Protocol-typed Cache interface; on a miss, PostgresUrlRepository.get() runs a parameterized query against the urls table using the short_code index, the result populates the cache with a TTL capped by the URL's expiry, and a RedirectResponse is returned.

## Scalability

Q: How would you scale this from 1,000 to 1 million users?
The backend is already stateless (JWT auth, no server-side sessions) so it scales horizontally behind a load balancer with no code changes. The bottleneck shifts to Postgres -- mitigated first by the Redis cache absorbing most redirect reads, then by read replicas if writes/complex queries become the constraint.

Q: How would you handle 100 million redirects per day?
That's ~1,157 req/s average, spikier in practice. The redirect path is already optimized: cache-aside means most requests never touch Postgres, and click-event recording is fully decoupled from the redirect via the queue, so analytics volume doesn't threaten redirect latency.

## Database

Q: What happens when PostgreSQL becomes the bottleneck?
First diagnose reads vs writes. Reads scale via replicas. Writes scale via reducing volume (already done for clicks) or partitioning.

Q: How would you partition the database?
click_events is the highest-volume table and a natural partitioning candidate -- by time range, since analytics queries are typically time-bounded and old partitions can be archived independently.

## Cache

Q: What happens if Redis fails?
Every cache-touching path fails open -- verified by actually stopping the Redis service and confirming redirects still succeed, in single-digit milliseconds, by falling through to Postgres. Rate limiting also fails open.

## Distributed Systems

Q: How would multiple backend instances share state?
They don't hold instance-local state that matters -- auth is stateless (JWT), the cache and rate limiter live in shared Redis, the database is the single source of truth. A deliberate design constraint from Phase 1 (e.g. choosing random short-code generation specifically to avoid a shared, contended counter).

Q: How would you handle concurrent requests?
The concrete example built and tested: two requests racing to claim the same custom alias. Handled via a database UNIQUE constraint, not an application-level lock (which wouldn't coordinate across processes anyway) -- proven with a real multi-threaded test using threading.Barrier to force genuine simultaneous inserts against real Postgres.

## Analytics

Q: Why is analytics asynchronous?
Measured, not assumed: a synchronous click-event write added real latency to every redirect (Phase 5) and coupled redirect availability to Postgres's health.

Q: How do you prevent duplicate events?
RabbitMQ's at-least-once delivery means redelivery is expected. A UUID (event_id), enforced unique at the database level via INSERT ... ON CONFLICT DO NOTHING, makes redelivery a safe no-op -- tested with 10 simulated redeliveries producing exactly one row.

## Reliability

Q: What happens when one service crashes?
- Redis down: redirects still work (fail-open cache), just slower.
- RabbitMQ down at publish time: that one click event is lost (logged), redirect still succeeds.
- Worker crashes mid-processing: manual ack means redelivery, idempotent insert means no duplicate.
- Postgres down: redirects fail -- the one dependency without a fail-open path, correctly so (can't redirect to a URL you can't look up).

## Security

Q: How do you prevent malicious URLs?
Current: scheme/format validation rejects obviously malformed input. Not yet implemented: a real phishing/malware blocklist check -- documented as a known gap.

Q: How do you prevent open redirects?
The redirect target is always looked up from our own database by our own generated short_code -- never taken directly from unvalidated user input as a redirect target.

## Performance

Q: How did you reduce redirect latency?
Redis cache-aside for the hot lookup, and moving click-event recording (DB write and CPU-bound parsing) off the synchronous request path. Measured, with honest reporting of what improved vs what didn't.
