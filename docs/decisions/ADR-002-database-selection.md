# ADR-002: Database Selection

## Context
Need durable storage for URLs, users, and click events, with real relationships (ownership, foreign keys) and the ability to enforce uniqueness atomically under concurrent writes.

## Options Considered
- **PostgreSQL**: relational, ACID transactions, rich type system, strong standards compliance, free, Docker-friendly.
- **MongoDB**: flexible schema, good for unstructured/rapidly-changing documents, weaker multi-document transaction guarantees historically, no native foreign key enforcement.
- **MySQL**: comparable relational option; close call against Postgres, mostly a "which is more the current SDE-interview default" choice.

## Decision
PostgreSQL.

## Reason
The data is inherently relational: `users` → `urls` → `click_events`, with real foreign keys and uniqueness constraints. The single most important correctness mechanism in this entire project — the `UNIQUE` constraint on `urls.short_code` that makes concurrent short-code generation safe — depends on atomic constraint enforcement at the database layer, which is a first-class relational-database feature, not something a document store gives for free. Concurrent custom-alias claims and idempotent click-event processing both lean on the same mechanism.

## Trade-offs
- Postgres requires a fixed(ish) schema and migrations for changes — slower to iterate on shape changes than a schema-less store, though our migration-file-per-change workflow keeps this manageable and is arguably a feature (forces deliberate schema evolution — see migrations 001-005, each with a documented reason).
- A single Postgres instance is a single point of write contention at very high scale — addressed conceptually below, not yet implemented (no current load justifies it).

## What we specifically use it for
- `urls`, `users`: strongly consistent, transactional writes, uniqueness enforcement.
- `click_events`: high write volume, FK to `urls.id`, idempotent inserts via `event_id` UNIQUE constraint.

## Future
At genuine scale: read replicas for the (much higher-volume) redirect-adjacent reads, and/or partitioning `click_events` by time range once analytics query patterns are well understood from real dashboard usage.
