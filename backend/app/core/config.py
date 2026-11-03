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
