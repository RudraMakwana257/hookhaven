import uuid
from datetime import timedelta

import pytest

from app.core.errors import InvalidTokenError
from app.core.security import (
    api_key_matches,
    create_token,
    decode_token,
    generate_api_key,
    hash_password,
    parse_api_key_prefix,
    verify_password,
)


def test_password_hash_verifies_and_is_salted() -> None:
    first, second = hash_password("s3cret-pass"), hash_password("s3cret-pass")
    assert first != second
    assert verify_password("s3cret-pass", first)
    assert not verify_password("wrong", first)
    assert not verify_password("s3cret-pass", "not-a-hash")


def test_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    assert decode_token(create_token(user_id, "access"), "access") == user_id


def test_token_type_is_enforced() -> None:
    token = create_token(uuid.uuid4(), "access")
    with pytest.raises(InvalidTokenError):
        decode_token(token, "refresh")


def test_expired_and_tampered_tokens_are_rejected() -> None:
    expired = create_token(uuid.uuid4(), "access", ttl=timedelta(seconds=-5))
    with pytest.raises(InvalidTokenError):
        decode_token(expired, "access")
    good = create_token(uuid.uuid4(), "access")
    with pytest.raises(InvalidTokenError):
        decode_token(good[:-2] + "xx", "access")


def test_api_key_generation_and_matching() -> None:
    key = generate_api_key()
    assert key.full_key.startswith(f"hh_{key.prefix}_")
    assert parse_api_key_prefix(key.full_key) == key.prefix
    assert api_key_matches(key.full_key, key.key_hash)
    assert not api_key_matches(key.full_key + "x", key.key_hash)
    assert key.full_key not in key.key_hash


@pytest.mark.parametrize("bad", ["", "hh_", "hh_abc", "xx_abc_def", "nounderscores"])
def test_malformed_api_keys_have_no_prefix(bad: str) -> None:
    assert parse_api_key_prefix(bad) is None
