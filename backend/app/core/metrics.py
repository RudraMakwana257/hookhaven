"""Prometheus metrics. Labels are kept low-cardinality on purpose: route *templates*
(`/v1/endpoints/{endpoint_id}`), never raw paths or ids."""

from prometheus_client import Counter, Gauge, Histogram

_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

HTTP_REQUESTS = Counter(
    "hookhaven_http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "hookhaven_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=_HTTP_BUCKETS,
)
EVENTS_INGESTED = Counter(
    "hookhaven_events_ingested_total",
    "Events accepted by POST /v1/events (result: accepted | replayed)",
    ["result"],
)

DELIVERY_ATTEMPTS = Counter(
    "hookhaven_delivery_attempts_total",
    "Delivery attempts by outcome (success, http_4xx, http_5xx, timeout, network_error, "
    "blocked, config_error, endpoint_disabled)",
    ["outcome"],
)
DELIVERY_ATTEMPT_DURATION = Histogram(
    "hookhaven_delivery_attempt_duration_seconds",
    "Time spent calling the customer's endpoint",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0),
)
DELIVERIES_FINALIZED = Counter(
    "hookhaven_deliveries_finalized_total",
    "Delivery state transitions after an attempt (succeeded | retry_scheduled | dead)",
    ["result"],
)
ENDPOINTS_AUTO_DISABLED = Counter(
    "hookhaven_endpoints_auto_disabled_total", "Endpoints disabled after repeated failures"
)
RECONCILER_REQUEUED = Counter(
    "hookhaven_reconciler_requeued_total", "Overdue deliveries pushed back onto the queue"
)

RATE_LIMITED = Counter("hookhaven_rate_limited_total", "Requests rejected with 429", ["scope"])
RATE_LIMITER_ERRORS = Counter(
    "hookhaven_rate_limiter_errors_total", "Rate limiter could not reach Redis"
)

# Set at scrape time (see api/metrics.py)
QUEUE_DEPTH = Gauge("hookhaven_queue_depth", "Delivery ids per queue state", ["state"])
DELIVERY_LAG = Gauge(
    "hookhaven_delivery_lag_seconds",
    "How long the most overdue pending delivery has been waiting (0 = nothing overdue)",
)
SCRAPE_ERRORS = Counter(
    "hookhaven_metrics_scrape_errors_total", "Gauges that could not be refreshed", ["source"]
)
BUILD_INFO = Gauge("hookhaven_build_info", "Running version", ["version"])
