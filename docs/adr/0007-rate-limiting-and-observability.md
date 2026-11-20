# ADR 0007: Rate limiting and observability

## Status
Accepted

## Rate limiting
**Algorithm: token bucket in Redis, one Lua script.** A bucket holds `burst` tokens and refills at a
steady rate; each request spends one. Clients get bursts, the long-run rate is bounded, and (unlike
fixed windows) there is no spike at window boundaries. Read-refill-spend-write is atomic, so many
API replicas share one correct count (a test fires 50 concurrent requests at a 5-token bucket and
exactly 5 pass). Idle buckets expire on their own.

| Scope | Keyed by | Why |
|---|---|---|
| `api` | user id | Management API. JWT and API key share one budget, so alternating credentials does not double it. |
| `ingest` | user id | `POST /v1/events` has its own, larger bucket: a heavy producer cannot lock itself out of the management API, and vice versa. |
| `auth-ip` | client IP | register / login / refresh, checked *before* any password hashing, so floods cost no CPU. |
| `login-email` | hash of the email | A botnet rotating IPs still hits a per-account limit. Hashed so no addresses live in Redis keys. |

**Authenticated limits are keyed by user id *after* authentication.** Limiting on the raw API key (or
its public `hh_<prefix>`) before auth would let anyone drain a victim's budget with bogus keys that
carry the victim's prefix. A test sends 10 forged requests and shows the victim is unaffected.

**Client IP.** `X-Forwarded-For` is ignored unless `TRUSTED_PROXY_COUNT` says how many proxies append
to it; then the Nth entry from the right is used, which a client cannot forge by prepending fake
addresses. Both behaviours are tested.

**Responses.** `429` with `Retry-After` and `RateLimit-Limit` / `RateLimit-Remaining` (also on
successful responses).

**Failure mode: fail open by default** (`RATE_LIMIT_FAIL_OPEN`). If Redis is unreachable the API keeps
serving; `hookhaven_rate_limiter_errors_total` makes that visible. Trade-off: login throttling is
unavailable during a Redis outage. Set it to `false` to fail closed (503) if you prefer that.

## Observability
- **Metrics (Prometheus):** `/metrics` on the API (optional bearer token, required in production) and
  a listener on the worker (`WORKER_METRICS_PORT`, internal network only). See
  [`docs/observability.md`](../observability.md) for the catalogue and alert examples.
- **Cardinality discipline.** HTTP metrics are labelled with the route *template*
  (`/v1/endpoints/{endpoint_id}`), built from the matched path and its path params. Unknown paths
  collapse into one `unmatched` label, so scanners cannot create unbounded series.
- **State gauges are computed at scrape time** (queue depth from Redis, delivery lag from Postgres).
  If a dependency is down the scrape still succeeds and a scrape-error counter increments: monitoring
  must not go blind exactly when something breaks.
- **`hookhaven_delivery_lag_seconds`** is the headline SLO signal: how long the most overdue pending
  delivery has been waiting. Healthy is ~0.
- **Errors:** unhandled exceptions are logged with a stack trace and the client receives a generic
  500 containing only a `request_id` that matches the logs. Internals never leak (tested).
- **Request ids** supplied by clients are accepted only if they match `[A-Za-z0-9._-]{1,64}`; they end
  up in logs and headers, so hostile values are replaced.
- **Security headers:** `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and
  `Cache-Control: no-store` on `/v1/*` (responses carry tokens and secrets).

## Limitations
- Single-process metrics registry. Running several uvicorn/gunicorn workers per container would need
  `prometheus_client`'s multiprocess mode; we run one process per container.
- Limiter timestamps come from the API host's clock; replicas should be NTP-synced. A clock that moves
  backwards never mints tokens (tested).
- No distributed tracing yet (OpenTelemetry would be the next step).
