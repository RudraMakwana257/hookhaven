"""Symmetric encryption for secrets we must be able to read back (webhook signing secrets)."""

from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet

from app.core.config import get_settings


@lru_cache
def _fernet_for(keys: str) -> MultiFernet:
    return MultiFernet([Fernet(k.strip()) for k in keys.split(",") if k.strip()])


def encrypt_secret(plaintext: str) -> str:
    return _fernet_for(get_settings().encryption_keys).encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Raises `cryptography.fernet.InvalidToken` if the token is corrupt or the key is unknown."""
    return _fernet_for(get_settings().encryption_keys).decrypt(token.encode()).decode()
