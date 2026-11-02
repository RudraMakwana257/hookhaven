import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings

GOOD = {
    "environment": "production",
    "jwt_secret": "x" * 40,
    "encryption_keys": Fernet.generate_key().decode(),
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
