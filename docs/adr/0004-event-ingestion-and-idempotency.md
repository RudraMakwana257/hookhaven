# ADR 0004: Durable-first event ingestion and idempotency

## Status
Accepted

## Decision
`POST /v1/events` does, in order:
1. Reject oversized payloads (Content-Length early, exact canonical size after parsing).
2. If an `Idempotency-Key` was sent, look for an existing event with that key (fast path).
3. In **one transaction**, insert the event and one `deliveries` row per matching active endpoint.
4. **Commit**, then push delivery ids to Redis, then return `202 Accepted`.

Deliveries are snapshotted at ingestion: editing or deleting an endpoint later does not change
where already-accepted events go (deleting cascades to that endpoint's deliveries).

## Failure modes
| Failure | Result |
|---|---|
| Crash after commit, before enqueue | Deliveries are durable and `pending`; the reconciler (phase 6) re-enqueues them. |
| Redis down during ingestion | Still `202`. The event is stored; enqueue failure is logged and reconciled later. |
| Enqueue *before* commit (rejected design) | A worker could pop an id whose row does not exist yet, or the commit could fail after the job was queued. |
| Client retries after a timeout | Same key + same payload returns the original event (`Idempotent-Replayed: true`), no new deliveries. |
| Same key, different payload | `409`. The key identifies one logical request; reuse is a client bug. |
| Two identical requests race | Both pass the SELECT; the `UNIQUE (user_id, idempotency_key)` constraint lets one commit. The loser catches `IntegrityError`, rolls back (its deliveries vanish too) and returns the winner's event. |

## Identity of a request
`payload_hash = sha256(canonical_json({type, payload}))` with sorted keys and no whitespace,
so reordering JSON keys does not make a retry look like a different request.

## Consequences
- The database constraint, not application logic, is the authority on uniqueness.
- Delivery is at-least-once; receivers should dedupe using the event/delivery id headers (phase 5).
- Idempotency keys are retained as long as the event is (no TTL yet).
- Fan-out matching happens in Python over the user's active endpoints (capped at 20 per user).
  If that cap is ever lifted, move matching into SQL.

## Lesson recorded
After `session.rollback()` all ORM instances are expired; touching `user.id` then raises
`MissingGreenlet` in async code. Scalars needed after a rollback are captured beforehand. A test
that forces the lost-race path exists specifically for this.
