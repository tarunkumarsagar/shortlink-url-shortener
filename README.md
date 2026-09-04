# ShortLink

A production-style URL shortener and analytics platform, built incrementally to demonstrate backend engineering and distributed-systems concepts.

**Status: Feature-complete MVP+ — URL shortening, auth, caching, async analytics pipeline with a reliable worker, and rate limiting. All built, tested, and verified against real infrastructure (not mocks).**



## Features

- URL shortening: random Base62 codes (cryptographically secure), custom aliases, expiration, soft-delete
- JWT authentication (access + refresh tokens), Argon2id password hashing
- Ownership model: anonymous creation supported (MVP requirement); only owners can delete/view analytics for owned URLs, enforced in the service layer
- Redis cache-aside on the redirect/metadata path, with TTL capped by URL expiry and explicit invalidation on delete
- Fully asynchronous analytics pipeline: redirect publishes a click event via RabbitMQ (after the response is sent, via FastAPI BackgroundTasks) → dedicated worker process consumes with manual ack, idempotent inserts (absorbs RabbitMQ's at-least-once redelivery), bounded retry, and dead-letter-queue routing for permanently broken messages
- Redis-backed sliding-window rate limiting, fails open on Redis outage
- Basic analytics endpoint + a static dashboard (`frontend/index.html`) with a device-breakdown chart
- Full CRUD + auth + analytics API, documented via FastAPI's auto-generated `/docs`

## Architecture

```
Client → FastAPI (auth + rate limit) → UrlService
                                          ├─ Redis (cache-aside)
                                          └─ PostgreSQL (source of truth)

Redirect → BackgroundTasks → RabbitMQ (click_events queue)
                                  ↓
                          Analytics Worker (manual ack, idempotent)
                                  ↓
                          PostgreSQL (click_events, event_id UNIQUE)
                                  ↓ (on failure, after bounded retry)
                          click_events.dlq
```

## What exists right now

- FastAPI backend, PostgreSQL-backed via `psycopg` with connection pooling
- Random Base62 short-code generation with collision retry, enforced atomically by a DB `UNIQUE` constraint — proven safe under real concurrent load via a multi-threaded test against live Postgres
- Base62 encode/decode utilities (documented alternative generation strategy)
- Custom alias support, URL expiration, soft-delete
- Full CRUD: create, redirect, fetch metadata, delete
- **Authentication**: register/login/refresh via JWT, Argon2id password hashing
- **Authorization**: anonymous or authenticated URL creation; ownership-gated delete and analytics access
- **Redis caching (cache-aside)**: proven with real Redis, including a fail-open test against a genuinely stopped Redis service
- **RabbitMQ analytics pipeline**: publish-after-response via BackgroundTasks, a full worker with manual ack / idempotent processing / dead-letter queue — every failure mode (malformed message, transient DB error, redelivery) verified live against real infrastructure, not just unit-tested in isolation
- **Rate limiting**: Redis sliding window, per-IP for anonymous / per-user for authenticated requests
- **Analytics endpoint + dashboard**: verified end-to-end through the full async pipeline
- Dependency-injected service layer — repository, cache, and message publisher all swappable behind `Protocol`s
- **117 passing tests**: unit tests, fast API-contract tests (in-memory doubles), and real Postgres/Redis/RabbitMQ/end-to-end integration tests
- Docker Compose for the full stack (Postgres, Redis, RabbitMQ, backend, worker), with a GitHub Actions CI pipeline running the same tests against real service containers

## Running it locally

**1. Set up your environment file:**
```bash
cp .env.example .env
```

**2. Start the full stack:**
```bash
docker compose up -d
```
This starts Postgres, Redis, RabbitMQ, the backend API, and the analytics worker. Migrations run automatically on backend startup.

**3. Open the app:**
- API docs: http://localhost:8000/docs
- Dashboard: open `frontend/index.html` directly in a browser (or serve it with any static file server)
- RabbitMQ management UI: http://localhost:15672 (guest/guest)

### Running the backend without Docker (for development)
```bash
cd backend
pip install -r requirements.txt
python3 -m app.core.migrate
python3 -m uvicorn app.main:app --reload
# In a separate terminal, run the worker:
python3 -m app.workers.worker
```

## Running tests

```bash
cd backend
python3 -m pytest -v
```

Most test files require running Postgres/Redis/RabbitMQ instances (via `docker compose up -d` first) — they test against real infrastructure by design, not mocks, because the whole point of this project is proving distributed-systems behavior actually works. `test_api.py`, `test_auth_service.py`, `test_security.py`, `test_base62.py`, `test_code_generator.py`, `test_click_event_service.py`, `test_event_processor.py`, and `test_user_agent_parser.py` use in-memory doubles and stay fast/infra-independent.

## Honest status notes (per the project's "no fake completion" rule)

- **Not yet load tested**: no Locust/k6 results exist. The only performance numbers in this README/docs are small-scale, honestly-caveated local measurements (see `docs/decisions/ADR-006`), never claimed as production benchmarks.
- **Not yet deployed** to any cloud environment — this is a local-first, zero-cost project by design (see the Cost table below). Free-tier cloud deployment is a documented *optional* future step, not a requirement.
- **Geoip country enrichment** is a reserved, unpopulated database column — not implemented.
- **Malicious URL / phishing detection** is not implemented — only basic scheme/format validation exists today.

## Cost

| Component | Technology | Cost | Runs Locally? | Mandatory? |
|---|---|---|---|---|
| Backend | FastAPI | $0 | Yes | Yes |
| Database | PostgreSQL | $0 | Yes | Yes |
| Cache | Redis | $0 | Yes | Yes |
| Message Queue | RabbitMQ | $0 | Yes | Yes |
| Analytics Worker | Python | $0 | Yes | Yes |
| Frontend | Static HTML/JS | $0 | Yes | Yes |
| CI | GitHub Actions | $0* | Cloud | Yes |
| Deployment | Any free-tier PaaS | $0* | Cloud | No |

