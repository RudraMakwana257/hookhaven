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
        if self.delivery_max_attempts < 1 or self.worker_concurrency < 1:
            raise ValueError("DELIVERY_MAX_ATTEMPTS and WORKER_CONCURRENCY must be >= 1")

        if self.environment == "production":
            if self.jwt_secret == _INSECURE_DEFAULT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be set to a random value of 32+ characters")
            if _INSECURE_DEFAULT_ENCRYPTION_KEY in keys:
                raise ValueError("ENCRYPTION_KEYS must not use the development default")
            if self.allow_private_destinations:
                raise ValueError("ALLOW_PRIVATE_DESTINATIONS must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
