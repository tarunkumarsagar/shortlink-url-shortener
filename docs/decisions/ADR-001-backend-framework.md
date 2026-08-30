# ADR-001: Backend Framework Selection

## Context
Needed a backend language/framework for a service whose hottest path (redirect) is I/O-bound: cache lookup, possible DB fallback, message queue publish.

## Options Considered
- **Python + FastAPI**: async-native, Pydantic validation for free, fast iteration, huge industry adoption.
- **Java + Spring Boot**: extremely common in SDE job requirements, mature ecosystem, strong typing, heavier boilerplate.
- **C++**: best raw performance, deep OOP/DSA demonstration, but wrong tool for a web CRUD+auth+ORM service — time would go to fighting HTTP libraries instead of learning systems concepts.

## Decision
FastAPI (Python), synchronous route handlers (not async def) for simplicity of reasoning about the connection pool and thread-safety of pika/psycopg clients across FastAPI's threadpool execution model.

## Reason
The redirect path is I/O-bound (cache lookup, DB fallback, queue publish), so an async-capable framework gets high concurrency without needing a compiled language. Pydantic's validation eliminates a whole category of manual input-checking boilerplate, letting engineering time go to the distributed-systems parts of the project (caching, queueing, idempotency) rather than framework plumbing.

## Trade-offs
- Python's raw throughput per core is lower than Java/C++ — acceptable at this project's scale; would need re-evaluation at genuinely high (>10K req/s per instance) throughput.
- Chose sync route handlers over async def to avoid mixing sync DB/queue clients (psycopg, pika) with an async event loop, which would require either async drivers throughout or explicit thread-pool offloading. This is a deliberate simplicity-over-marginal-performance choice, revisited if profiling ever shows request-handling concurrency as the bottleneck rather than I/O wait.

## Future
If profiling showed CPU-bound work becoming the bottleneck (not the case today — user-agent parsing was explicitly moved OFF the hot path in Phase 6 for exactly this reason), a compiled-language rewrite of just the redirect endpoint would be a scoped, honest next step — not a full-system rewrite.
