import base64
from functools import lru_cache
from typing import Literal, Self

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULT_SECRET = "dev-only-secret-change-me-dev-only-secret"  # noqa: S105
_INSECURE_DEFAULT_ENCRYPTION_KEY = base64.urlsafe_b64encode(
    b"dev-only-encryption-key-32-bytes"
).decode()


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://hookhaven:hookhaven@localhost:5432/hookhaven"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = _INSECURE_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7

    # Comma-separated Fernet keys. The first encrypts; all can decrypt (enables key rotation).
    encryption_keys: str = _INSECURE_DEFAULT_ENCRYPTION_KEY

    # Local-development escape hatch for delivering to localhost / private networks.
    # Refused in production (see validator).
    allow_private_destinations: bool = False
    max_endpoints_per_user: int = 20
    max_event_payload_bytes: int = 256 * 1024

    # Delivery worker
    worker_concurrency: int = 10
    delivery_timeout_seconds: float = 10.0
    delivery_lease_seconds: int = 60
    delivery_max_attempts: int = 7  # 1 initial attempt + 6 retries

    # Reconciler: re-enqueue pending deliveries the queue has lost track of
    reconcile_interval_seconds: float = 30.0
    reconcile_grace_seconds: int = 120  # how overdue a delivery must be before we step in
    reconcile_batch_size: int = 500

    # Rate limiting (token buckets in Redis). "burst" = bucket size, the rest is the refill rate.
    rate_limit_enabled: bool = True
    rate_limit_fail_open: bool = True  # if Redis is down: keep serving (True) or reject (False)
    rate_limit_api_burst: int = 60
    rate_limit_api_per_second: float = 20.0
    rate_limit_ingest_burst: int = 200
    rate_limit_ingest_per_second: float = 50.0
    rate_limit_auth_ip_burst: int = 10  # register / login / refresh, per client IP
    rate_limit_auth_ip_refill_seconds: float = 6.0  # one token back every N seconds
    rate_limit_login_email_burst: int = 5  # login attempts per account, from any IP
    rate_limit_login_email_refill_seconds: float = 60.0
    # How many reverse proxies sit in front of us. 0 = ignore X-Forwarded-For (it is spoofable).
    trusted_proxy_count: int = 0

    # Browser access (the dashboard lives on another origin). Comma-separated, no trailing slash.
    cors_allowed_origins: str = ""

    # Live updates (Server-Sent Events)
    sse_max_lifetime_seconds: float = 600.0  # also capped by the access token's remaining life
    sse_heartbeat_seconds: float = 15.0
    sse_max_streams_per_user: int = 5

    # Prometheus
    metrics_enabled: bool = True
    metrics_token: str | None = None  # if set, /metrics requires "Authorization: Bearer <token>"
    worker_metrics_port: int = 9100  # 0 disables the worker's metrics listener

    # Endpoint is auto-disabled after this many consecutive dead deliveries (no success between)
    endpoint_disable_threshold: int = 10

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _validate_secrets(self) -> Self:
        keys = [k.strip() for k in self.encryption_keys.split(",") if k.strip()]
        if not keys:
            raise ValueError("ENCRYPTION_KEYS must contain at least one key")
        for key in keys:
            try:
                Fernet(key)
            except ValueError:
                raise ValueError("ENCRYPTION_KEYS must be comma-separated Fernet keys") from None

        # send_webhook's overall deadline is 2x the timeout; the lease must outlive it, otherwise
        # another worker could be handed a delivery that is still in flight.
        if self.delivery_lease_seconds <= 2 * self.delivery_timeout_seconds + 5:
            raise ValueError("DELIVERY_LEASE_SECONDS must exceed 2x DELIVERY_TIMEOUT_SECONDS + 5")
        if (
            min(
                self.delivery_max_attempts, self.worker_concurrency, self.endpoint_disable_threshold
            )
            < 1
        ):
            raise ValueError(
                "DELIVERY_MAX_ATTEMPTS, WORKER_CONCURRENCY and ENDPOINT_DISABLE_THRESHOLD "
                "must be >= 1"
            )

        for origin in self.cors_origins:
            if origin != "*" and not (
                origin.startswith(("http://", "https://")) and not origin.endswith("/")
            ):
                raise ValueError(f"CORS origin {origin!r} must look like https://app.example.com")
        if (
            self.sse_max_streams_per_user < 1
            or min(self.sse_max_lifetime_seconds, self.sse_heartbeat_seconds) <= 0
        ):
            raise ValueError("SSE limits must be positive")

        rates = (
            self.rate_limit_api_per_second,
            self.rate_limit_ingest_per_second,
            self.rate_limit_auth_ip_refill_seconds,
            self.rate_limit_login_email_refill_seconds,
        )
        bursts = (
            self.rate_limit_api_burst,
            self.rate_limit_ingest_burst,
            self.rate_limit_auth_ip_burst,
            self.rate_limit_login_email_burst,
        )
        if min(rates) <= 0 or min(bursts) < 1:
            raise ValueError("rate limit bursts must be >= 1 and rates/intervals must be > 0")

        if self.environment == "production":
            if self.jwt_secret == _INSECURE_DEFAULT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be set to a random value of 32+ characters")
            if _INSECURE_DEFAULT_ENCRYPTION_KEY in keys:
                raise ValueError("ENCRYPTION_KEYS must not use the development default")
            if self.allow_private_destinations:
                raise ValueError("ALLOW_PRIVATE_DESTINATIONS must be false in production")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ALLOWED_ORIGINS must list explicit origins in production")
            if self.metrics_enabled and not self.metrics_token:
                raise ValueError(
                    "METRICS_TOKEN must be set in production (or METRICS_ENABLED=false)"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
