# ADR-003: Cache Selection (Redis)

## Context
The redirect lookup (`short_code` -> `long_url`) is the hottest, most latency-sensitive read in the system, run on every single click. Hitting Postgres for every redirect wastes connection-pool capacity on a workload that's overwhelmingly repeat reads of a small hot set.

## Options Considered
- **Redis**: in-memory key-value store, sub-millisecond lookups, widely used for exactly this cache-aside pattern, also reusable for rate limiting.
- **In-process memory cache** (e.g. a Python dict with TTL): zero network hop, but doesn't work across multiple backend instances -- each process would have its own inconsistent view, defeating the point once horizontally scaled.
- **Memcached**: comparable raw caching performance, but no built-in data structures (sorted sets) needed for the sliding-window rate limiter, so we'd need two different systems for two closely related jobs.

## Decision
Redis, used for both the redirect cache (Phase 4) and rate limiting (Phase 8).

## Reason
Redis is the one piece of state naturally shared by every backend instance, which is required correctness for both jobs: a redirect cache that's instance-local would serve different data depending which instance handled the request, and a rate limiter with instance-local counters would let N instances each grant their own full quota. Redis's sorted-set data structure is also what makes the sliding-window rate limiter clean to implement (ZREMRANGEBYSCORE + ZCARD + ZADD).

## Trade-offs
- Adds an operational dependency and a genuine failure mode. Mitigated by designing every Redis-touching code path (cache, rate limiter) to fail open: a Redis outage degrades to "no caching" / "no rate limiting," not "the API is down." Verified directly by killing the Redis process mid-test and confirming redirects still succeeded, fast.
- Cache invalidation is a real correctness surface: UrlService.delete() explicitly invalidates the cache key on delete -- proven via direct Redis inspection in tests, not just trusted.

## What we specifically use it for
- Cache-aside on UrlService.resolve() (redirect + metadata lookups), TTL capped by the URL's own expires_at.
- Sliding-window rate limiting per IP/user identity.

## Future
Cache invalidation on URL update (not yet a feature) would need the same explicit-invalidation treatment as delete. At much higher scale, Redis Cluster for horizontal cache capacity is the natural next step.
