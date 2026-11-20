# Observability

Logs are structured JSON (human-readable in development) with a `request_id` on every line.
Metrics are Prometheus. `docker compose --profile monitoring up` adds a Prometheus on :9090.

## Metrics

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `hookhaven_http_requests_total` | counter | method, route, status | Requests by route template |
| `hookhaven_http_request_duration_seconds` | histogram | method, route | API latency |
| `hookhaven_events_ingested_total` | counter | result (`accepted`/`replayed`) | Ingestion, idempotent replays |
| `hookhaven_delivery_attempts_total` | counter | outcome | `success`, `http_4xx`, `http_5xx`, `timeout`, `network_error`, `blocked`, `config_error`, `endpoint_disabled` |
| `hookhaven_delivery_attempt_duration_seconds` | histogram | | Time spent calling customer endpoints |
| `hookhaven_deliveries_finalized_total` | counter | result | `succeeded`, `retry_scheduled`, `dead` |
| `hookhaven_endpoints_auto_disabled_total` | counter | | Endpoints disabled after repeated failures |
| `hookhaven_reconciler_requeued_total` | counter | | Work the queue had lost, re-enqueued |
| `hookhaven_rate_limited_total` | counter | scope | 429s |
| `hookhaven_rate_limiter_errors_total` | counter | | Limiter could not reach Redis |
| `hookhaven_queue_depth` | gauge | state (`ready`/`scheduled`/`processing`) | Backlog |
| `hookhaven_delivery_lag_seconds` | gauge | | Age of the most overdue pending delivery |
| `hookhaven_metrics_scrape_errors_total` | counter | source | A gauge could not be refreshed |

The API process exposes the HTTP, ingestion, rate-limit and state-gauge metrics; the worker process
exposes the delivery, reconciler and auto-disable metrics.

## Example alerts (PromQL)

```promql
# Deliveries are late: the most overdue one has waited over 2 minutes, for 5 minutes
hookhaven_delivery_lag_seconds > 120

# Customer endpoints are failing broadly (more than half of attempts, last 10 min)
sum(rate(hookhaven_delivery_attempts_total{outcome!~"success|endpoint_disabled"}[10m]))
  / sum(rate(hookhaven_delivery_attempts_total{outcome!="endpoint_disabled"}[10m])) > 0.5

# Work is being lost by the queue (reconciler keeps having to rescue it)
increase(hookhaven_reconciler_requeued_total[15m]) > 10

# API 5xx ratio
sum(rate(hookhaven_http_requests_total{status=~"5.."}[5m]))
  / sum(rate(hookhaven_http_requests_total[5m])) > 0.02

# p95 API latency
histogram_quantile(0.95, sum by (le) (rate(hookhaven_http_request_duration_seconds_bucket[5m]))) > 1

# Redis trouble: limiter failing open
increase(hookhaven_rate_limiter_errors_total[5m]) > 0
```
