Technology: PostgreSQL
Why? Relational data model, atomic UNIQUE constraints are the core mechanism preventing short-code collisions under concurrency.
Why not MongoDB? No native atomic uniqueness enforcement across concurrent writes; would need to simulate FK relationships at the app layer.
Why not MySQL? Close call -- Postgres has a richer type system and is the more common current SDE-interview default.
What problem does it solve? Durable, consistent, relationally-correct storage with transaction guarantees.
What trade-off did we accept? Fixed(ish) schema requiring migrations -- mitigated by a deliberate migration-per-change workflow.
What would we use at massive scale? Read replicas, then time-based partitioning of click_events.

Technology: Redis
Why? Shared, sub-millisecond state across backend instances for caching and rate limiting.
Why not just hit Postgres every time? Redirect is the hottest read path; caching avoids wasting connection-pool capacity on repeat lookups.
What problem does it solve? Redirect latency, and correctness of rate limiting across multiple instances.
What trade-off did we accept? An operational dependency and a new failure mode -- mitigated by fail-open design, verified by actually killing the service mid-test.
What would we use at massive scale? Redis Cluster for horizontal cache capacity.

Technology: RabbitMQ
Why? Decouples the redirect (fast, must-work) from analytics (can be delayed, must-be-reliable), with ack/retry/DLQ semantics matching our reliability needs.
Why not Kafka? Kafka's value (replay, huge throughput, independent consumer groups) isn't needed at this scale.
What problem does it solve? Reliable, decoupled, asynchronous click-event processing.
What trade-off did we accept? No event replay; connection-per-publish is measurably slower in raw terms than the synchronous write it replaced -- the benefit is architectural decoupling, not raw speed.
What would we use at massive scale? Kafka, if replay or independent multi-consumer streaming became a real requirement.

Technology: FastAPI
Why? Async-capable for an I/O-bound hot path, Pydantic validation for free, fast iteration.
Why not Spring Boot? More boilerplate, slower to iterate as a learning project.
Why not raw C++? Wrong tool for CRUD+auth+ORM work; time would go to fighting HTTP libraries instead of distributed-systems concepts.
What problem does it solve? Handling concurrent I/O-bound requests efficiently with minimal boilerplate.
What trade-off did we accept? Lower raw per-core throughput than a compiled language -- acceptable at this scale.
What would we use at massive scale? Possibly a compiled-language rewrite of just the redirect hot path, if profiling showed it as the bottleneck.

Technology: JWT + Argon2id
Why? Stateless auth scales horizontally with zero shared session storage; Argon2id is OWASP's current recommended password hash.
Why not server-side sessions? Requires shared session storage across instances, reintroducing shared-state coordination.
What problem does it solve? Horizontally-scalable authentication and secure password storage.
What trade-off did we accept? Pure JWTs can't be revoked before expiry without extra infrastructure -- mitigated by short access-token expiry.
What would we use at massive scale? Same approach, adding a Redis-backed token-revocation denylist if real need arises.
