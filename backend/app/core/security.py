"""Password hashing, JWT handling and API key primitives.

Pure functions only: no database or HTTP knowledge lives here.
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.errors import InvalidTokenError

TokenType = Literal["access", "refresh"]

_hasher = PasswordHasher()
# Used to spend comparable time when the email is unknown (prevents user enumeration by timing).
_DUMMY_HASH = _hasher.hash("hookhaven-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def create_token(user_id: uuid.UUID, token_type: TokenType, *, ttl: timedelta | None = None) -> str:
    settings = get_settings()
    if ttl is None:
        ttl = (
            timedelta(minutes=settings.access_token_ttl_minutes)
            if token_type == "access"  # noqa: S105
            else timedelta(days=settings.refresh_token_ttl_days)
        )
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "type": token_type, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType) -> uuid.UUID:
    """Validate signature, expiry and token type; return the user id."""
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
        if claims["type"] != expected_type:
            raise InvalidTokenError("wrong token type")
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc


# ---- API keys: "hh_<prefix>_<secret>". Only a SHA-256 of the key is stored. ----
# SHA-256 (not argon2) is appropriate: keys are 256-bit random, so brute force is infeasible
# and verification must be fast because it happens on every API request.

API_KEY_NAMESPACE = "hh"


@dataclass(frozen=True)
class GeneratedApiKey:
    full_key: str
    prefix: str
    key_hash: str


def generate_api_key() -> GeneratedApiKey:
    prefix = secrets.token_hex(4)
    full_key = f"{API_KEY_NAMESPACE}_{prefix}_{secrets.token_urlsafe(32)}"
    return GeneratedApiKey(full_key, prefix, hash_api_key(full_key))


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def parse_api_key_prefix(key: str) -> str | None:
    parts = key.split("_", 2)
    if len(parts) != 3 or parts[0] != API_KEY_NAMESPACE or not parts[1] or not parts[2]:
        return None
    return parts[1]


def api_key_matches(key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(key), stored_hash)
