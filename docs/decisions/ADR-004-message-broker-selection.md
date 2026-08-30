# ADR-004: Message Broker Selection

## Context
Redirects must not be slowed down or made fragile by analytics processing (parsing user-agents, writing click_events). Need to decouple the "must happen now, fast" redirect from the "can happen slightly later, reliably" analytics write.

## Options Considered
- **Kafka**: partitioned, replayable log, massive throughput, consumer groups with offset tracking. Industry-standard for high-volume event streaming.
- **RabbitMQ**: traditional broker (exchanges/queues/routing), built-in dead-letter queue support, acknowledgment/retry semantics, lighter operational footprint.
- **Redis Streams**: lightweight, reuses infrastructure already in the stack, but weaker ecosystem and mixes cache/broker responsibilities in one system.

## Decision
RabbitMQ.

## Reason
Kafka's core value is partitioned replay and very high sustained throughput -- neither is a real need at this project's click-event volume. RabbitMQ gives the concepts that actually map to what the project's failure-handling requirements need: acknowledgment (manual ack after DB write succeeds), retry, and dead-letter queues (a message that fails processing after bounded retries lands in click_events.dlq for inspection -- verified via live testing: a deliberately malformed message was shown moving from the main queue to the DLQ in real time). These map directly to what most backend teams actually reach for day to day, without the operational complexity of running and tuning Kafka.

## Trade-offs
- No replay of historical events -- once a message is acked and consumed, it's gone from RabbitMQ. If a future feature needed to reprocess historical click data through a new consumer, RabbitMQ can't provide that; Kafka's log-based model can. This is the concrete scenario that would justify migrating.
- Chose connection-per-publish for the producer over a shared connection pool -- simpler and correct under FastAPI's threadpool concurrency model (pika's BlockingConnection isn't thread-safe to share), at a real, measured cost: slower in raw terms than the pooled Postgres write it replaced. Documented explicitly -- the benefit is architectural decoupling, not raw speed.

## What we specifically use it for
- click_events queue: redirect publishes (fire-and-forget, via FastAPI BackgroundTasks so it happens AFTER the response is sent), the analytics worker consumes with manual ack and idempotent processing (event_id UNIQUE constraint absorbs at-least-once redelivery).
- click_events.dlq: dead-letter destination for permanently malformed messages or messages that exhausted retries.

## Future
If click volume grew to genuinely require replay or multiple independent consumer systems reading the same stream, migrate to Kafka. Not justified today.
