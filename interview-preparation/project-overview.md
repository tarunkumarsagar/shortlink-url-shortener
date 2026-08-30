# Project Overview

**What it is**: A production-style URL shortener with an asynchronous analytics pipeline, built to demonstrate backend engineering, distributed systems, and system design skills rather than to just be another CRUD app.

**Core features**: URL shortening (random Base62 codes, custom aliases, expiration), JWT authentication with ownership-gated management, Redis-cached redirects, RabbitMQ-based async click-event pipeline with an idempotent, dead-letter-queue-backed worker, Redis sliding-window rate limiting.

**Tech stack**: Python/FastAPI, PostgreSQL, Redis, RabbitMQ, Docker Compose, GitHub Actions, pytest.

**What makes it a real portfolio project, not a tutorial clone**:
1. Every architectural decision has a measured or reasoned justification (see docs/decisions/).
2. Real concurrency bugs were built, reproduced, and fixed with genuine multi-threaded tests against live Postgres.
3. Failure modes are tested by actually causing the failure (stopping Redis, publishing malformed messages) and observing real behavior, not just asserting it should work.
4. The synchronous-to-asynchronous analytics evolution was built in that order deliberately, with the synchronous version's cost measured first.

See interview-preparation/project-defense.md for the talking-points version at four different lengths.
