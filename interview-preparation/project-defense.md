# Project Defense: "Tell me about your ShortLink project"

## 60-90 second answer

ShortLink is a URL shortener I built to go deep on backend and distributed-systems fundamentals rather than just ship a CRUD app. It's a FastAPI service backed by PostgreSQL, with Redis caching the redirect hot path and RabbitMQ decoupling analytics from the redirect itself.

The part I'm most proud of is how I got to the async analytics pipeline: I didn't just build it because "that's the architecture" -- I built the synchronous version first, measured its actual cost on the redirect (about +0.7ms mean, worse at the tail), and used that measurement as the real justification for moving to an async, queue-based pipeline with a dedicated worker.

Along the way I dealt with real concurrency problems -- two requests racing to claim the same custom alias, solved with a database UNIQUE constraint rather than an application lock, proven with an actual multi-threaded test against real Postgres -- and real reliability problems, like making click-event processing idempotent so RabbitMQ's at-least-once delivery guarantee can't create duplicate rows.

Everything's tested -- 117+ tests including real integration tests against live Postgres, Redis, and RabbitMQ, not just mocks -- and I intentionally broke things (stopped Redis mid-test, published malformed messages) to verify the failure-handling actually works, not just that it looks right on paper.

## 2-minute version
Add: JWT auth with access/refresh tokens and why (stateless, horizontally scalable), the ownership model (anonymous creation supported, only owners can delete, enforced in the service layer not just the API layer), and the rate limiter (Redis sliding window, fails open on Redis outage so a cache problem can't become a full API outage).

## 5-minute technical deep-dive
Add: the Repository Pattern payoff (swapped in-memory -> Postgres repository with zero service-layer changes, formalized via a Protocol), the exact concurrency fix for alias collisions (UNIQUE constraint + catching UniqueViolation, not a lock), the full click-event lifecycle (publish after response via BackgroundTasks, manual ack only after DB commit, dead-letter queue for permanently-broken messages, bounded retry for transient failures), and the honest benchmark story (Phase 6 didn't clearly win on raw latency in my measurements -- I found and explained why, rather than reporting a flattering number).

## 10-minute full walkthrough
Add: the full phase-by-phase evolution (in-memory -> Postgres -> auth -> Redis -> sync analytics -> async analytics -> reliable worker -> rate limiting), specific bugs found and fixed during development (FastAPI route-registration-order shadowing /health, a fail-open gap in ClickEventService caught by a real failing test, RabbitMQ queue-argument mismatches between producer and worker), the security posture (Argon2id hashing, anti-enumeration on login errors, ownership checks that can't be bypassed by a future non-HTTP entry point), and what's explicitly NOT done yet and why (load testing, geoip enrichment, monitoring dashboards) -- a deliberate, prioritized roadmap, not an oversight.
