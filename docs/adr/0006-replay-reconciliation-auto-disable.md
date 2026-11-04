# ADR 0006: Replay, reconciliation and endpoint auto-disable

## Status
Accepted

## 1. Replay and the retry budget
A dead delivery can be replayed individually (`POST /v1/deliveries/{id}/replay`) or per endpoint
(`POST /v1/endpoints/{id}/replay-dead`, up to 1000 oldest first).

- Replay keeps the **same delivery id**, so receivers that dedupe on `Hookhaven-Delivery-Id` see one
  logical delivery that finally got through. Only dead deliveries are replayable: replaying a
  *succeeded* one would be silently dropped by such receivers.
- Attempt numbers keep growing (unique per delivery), but the retry budget is counted from
  `cycle_start_attempt`, the attempt count when the current cycle began. Without it, a replayed
  delivery (already at attempt N >= max) would die again after one try.
- The status flip is a compare-and-set (`UPDATE ... WHERE status = 'dead'`), so concurrent replays
  cannot both win. Replaying into a disabled endpoint is refused (409): it would just dead-letter
  again.
- Enqueue happens after the commit and is best-effort; the reconciler is the safety net.

## 2. Reconciler
Postgres is the truth, Redis a transport. Anything the transport loses is healed by one rule:
*a delivery that is `pending` and overdue by more than a grace period (default 120 s) is pushed
back on the queue.* Covers: Redis wiped or failed over, crash between commit and enqueue, worker
death. Properties:

- **Safe to duplicate.** The worker's compare-and-set reservation makes extra queue entries
  harmless, so the reconciler can err on the side of re-enqueueing.
- **No duplicate floods.** Re-queued ids get a TTL marker (= grace period), so a backlog draining
  slowly is not re-pushed every sweep.
- **One sweeper.** A Redis lock (`SET NX EX`) elects a single replica per interval.
- **Bounded.** At most `RECONCILE_BATCH_SIZE` per sweep, oldest first.

Limitation: if the oldest `batch_size` overdue deliveries stay stuck, they occupy the batch until
their markers expire. In practice stuck work is retried by the worker, so it leaves the set.

## 3. Endpoint auto-disable
Each delivery that ends `dead` through real failures increments `endpoints.consecutive_failures`
(atomic `UPDATE ... RETURNING`); any success resets it. At `ENDPOINT_DISABLE_THRESHOLD`
(default 10) the endpoint is disabled with a recorded reason. A dead delivery already represents
about 7 failed attempts over ~7.5 hours, so 10 in a row is a persistently broken receiver, and
retrying it forever just burns worker slots.

- Deliveries skipped *because* the endpoint is disabled do not inflate the counter.
- Re-enabling (`PATCH is_active=true`) clears the counter and the reason.
- Recovery workflow: fix the receiver -> re-enable -> `replay-dead`. Nothing is lost.
- A user disabling an endpoint by hand is recorded as `disabled by user`.

## Not done (tracked)
- Notifying the owner when an endpoint is auto-disabled (email or an in-app notice).
- Retention/pruning of old events and attempts.
