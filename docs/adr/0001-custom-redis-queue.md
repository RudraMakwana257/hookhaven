# ADR 0001: Custom Redis queue with Postgres as source of truth

## Status
Accepted

## Context
Hookhaven needs reliable, delayed, retryable job delivery. Options: Celery/ARQ,
or a small purpose-built queue on Redis.

## Decision
- **Postgres** stores events, deliveries and attempts. It is the source of truth.
- **Redis** is transport only:
  - a list for jobs ready now,
  - a sorted set (score = `next_attempt_at`) for delayed retries,
  - a lease (visibility timeout) so jobs held by a crashed worker are re-queued.
- Delivery is **at-least-once**; receivers dedupe using the idempotency key header.

## Consequences
- If Redis is lost, a reconciler re-enqueues due deliveries from Postgres. No data loss.
- We own the queue semantics (more code, more tests), but exactly the behaviour
  the product promises: backoff, DLQ, replay.
- Retry timing is exact and inspectable instead of hidden inside a framework.
