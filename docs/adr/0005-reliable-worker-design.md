# ADR 0005: Worker design: leases, compare-and-set attempts, at-least-once

## Status
Accepted

## Delivery loop
```
claim (READY -> PROCESSING, lease)
  -> reserve attempt N   (one UPDATE ... WHERE status='pending' AND due, RETURNING)
  -> sign + send         (SSRF-checked, IP-pinned, no redirects)
  -> COMMIT outcome      (attempt row + delivery status)
  -> ack | schedule_retry   (Redis, always AFTER the commit)
```

## Why it is safe
| Failure | What happens |
|---|---|
| Worker dies after claiming | Lease expires -> `reap_expired` re-queues. Its DB `next_attempt_at` (= lease deadline) also makes the delivery due again, so recovery does not depend on Redis alone. |
| Worker dies after sending, before commit | The delivery is re-sent: **at-least-once**. Receivers dedupe on `Hookhaven-Delivery-Id`, which is identical across retries. |
| Worker dies after commit, before ack/schedule | Lease expires, id re-queued, next worker sees the committed state: already `succeeded` -> ack; retry already decided -> parked until its due time. Nothing is sent early or twice. |
| Same id is queued twice / two workers hold it | The compare-and-set reservation lets exactly one proceed. Reserving also pushes `next_attempt_at` out to the lease deadline, which is the "in flight" marker. Without that, a second worker could reserve attempt N+1 while attempt N was still running (found by a test; see below). |
| Redis restarts and loses its data | Postgres still has every pending delivery. The reconciler (phase 6) re-enqueues from there. |
| Unexpected exception in processing | The lease is deliberately **not** acked; the delivery comes back after the lease expires instead of being silently dropped. |
| Redis unreachable | Consumers log and back off; they do not crash. |

## Redis transitions are Lua scripts
READY -> PROCESSING, PROCESSING -> SCHEDULED, SCHEDULED -> READY and the lease reaper each touch
two keys. As scripts they are atomic, so an id is never in zero places (lost) or two (duplicated).
Timestamps are passed as strings straight to ZADD; Lua numbers would be rounded to 14 digits.

## Retry policy
Delays 10s, 30s, 2m, 10m, 1h, 6h with +/-20% jitter; 7 attempts by default
(`DELIVERY_MAX_ATTEMPTS`). Jitter prevents a thundering herd when an endpoint recovers. When
attempts are exhausted the delivery becomes `dead` (the dead-letter queue is simply
`status = 'dead'`); phase 6 adds manual replay.

## Lease length
`DELIVERY_LEASE_SECONDS` must exceed 2x `DELIVERY_TIMEOUT_SECONDS` + 5 (the overall send
deadline). Settings validation refuses a configuration that violates this.

## Known limitations
- Ordering is not guaranteed (concurrent workers, retries). Receivers must not assume order.
- No per-endpoint concurrency cap yet: one slow endpoint can occupy all worker slots.
- Polling (0.5s) when idle, because a Lua claim cannot block. Acceptable latency for this
  workload; `BLMOVE` would be the alternative if sub-100ms pickup mattered.
- `next_attempt_at` doubles as the in-flight marker, so while an attempt runs it shows the lease
  deadline rather than a retry time.
