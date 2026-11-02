from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt_secret, encrypt_secret


@pytest.fixture(autouse=True)
def _reset_settings() -> Iterator[None]:
    yield
    get_settings.cache_clear()


def _use_keys(monkeypatch: pytest.MonkeyPatch, keys: str) -> None:
    monkeypatch.setenv("ENCRYPTION_KEYS", keys)
    get_settings.cache_clear()


def test_roundtrip_and_ciphertext_hides_plaintext() -> None:
    token = encrypt_secret("whsec_abc")
    assert "whsec_abc" not in token
    assert decrypt_secret(token) == "whsec_abc"


def test_encryption_is_randomised() -> None:
    assert encrypt_secret("same") != encrypt_secret("same")


def test_key_rotation_old_ciphertext_still_decrypts(monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    _use_keys(monkeypatch, old)
    token = encrypt_secret("secret")

    _use_keys(monkeypatch, f"{new},{old}")  # new key first: encrypts with new, decrypts both
    assert decrypt_secret(token) == "secret"
    assert decrypt_secret(encrypt_secret("fresh")) == "fresh"

    _use_keys(monkeypatch, new)  # old key retired
    with pytest.raises(InvalidToken):
        decrypt_secret(token)


def test_settings_reject_invalid_keys() -> None:
    with pytest.raises(ValueError, match="Fernet"):
        Settings(_env_file=None, encryption_keys="not-a-key")
