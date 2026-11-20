import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings

GOOD = {
    "environment": "production",
    "jwt_secret": "x" * 40,
    "encryption_keys": Fernet.generate_key().decode(),
    "metrics_token": "m" * 32,
}


def test_production_accepts_real_secrets() -> None:
    assert Settings(_env_file=None, **GOOD).environment == "production"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"jwt_secret": "short"}, "JWT_SECRET"),
        ({"allow_private_destinations": True}, "ALLOW_PRIVATE_DESTINATIONS"),
    ],
)
def test_production_refuses_unsafe_settings(override: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(_env_file=None, **{**GOOD, **override})  # type: ignore[arg-type]


def test_production_refuses_default_jwt_secret_and_encryption_key() -> None:
    dev = Settings(_env_file=None)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(_env_file=None, environment="production")
    with pytest.raises(ValueError, match="ENCRYPTION_KEYS"):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret="x" * 40,
            encryption_keys=dev.encryption_keys,
        )


def test_development_defaults_boot() -> None:
    assert Settings(_env_file=None).environment == "development"


def test_lease_must_outlive_the_delivery_deadline() -> None:
    with pytest.raises(ValueError, match="DELIVERY_LEASE_SECONDS"):
        Settings(_env_file=None, delivery_timeout_seconds=30, delivery_lease_seconds=60)
    Settings(_env_file=None, delivery_timeout_seconds=30, delivery_lease_seconds=120)


def test_production_requires_a_metrics_token_unless_metrics_are_off() -> None:
    without = {k: v for k, v in GOOD.items() if k != "metrics_token"}
    with pytest.raises(ValueError, match="METRICS_TOKEN"):
        Settings(_env_file=None, **without)  # type: ignore[arg-type]
    Settings(_env_file=None, metrics_enabled=False, **without)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "override",
    [
        {"rate_limit_api_burst": 0},
        {"rate_limit_api_per_second": 0},
        {"rate_limit_auth_ip_refill_seconds": -1},
    ],
)
def test_nonsensical_rate_limits_are_rejected(override: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="rate limit"):
        Settings(_env_file=None, **override)  # type: ignore[arg-type]
