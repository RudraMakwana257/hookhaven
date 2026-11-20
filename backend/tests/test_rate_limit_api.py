from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from prometheus_client import REGISTRY
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.deps import get_settings
from app.api.limits import get_rate_limiter
from app.core.config import Settings

from .conftest import register_and_login

SLOW = 0.0001  # refill so slow that no token comes back during a test
CREDS = {"email": "dev@example.com", "password": "correct-horse-1"}


def _limits(app: FastAPI, **overrides: Any) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, **overrides)


def _rate_limited(scope: str) -> float:
    return REGISTRY.get_sample_value("hookhaven_rate_limited_total", {"scope": scope}) or 0.0


async def test_api_budget_is_enforced_with_standard_headers(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _limits(app, rate_limit_api_burst=3, rate_limit_api_per_second=SLOW)
    before = _rate_limited("api")

    ok = [await client.get("/v1/endpoints", headers=auth_headers) for _ in range(3)]
    assert [r.status_code for r in ok] == [200, 200, 200]
    assert [r.headers["RateLimit-Remaining"] for r in ok] == ["2", "1", "0"]
    assert ok[0].headers["RateLimit-Limit"] == "3"

    blocked = await client.get("/v1/endpoints", headers=auth_headers)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Rate limit exceeded"}
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["RateLimit-Remaining"] == "0"
    assert _rate_limited("api") == before + 1


async def test_each_user_has_their_own_budget(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    other = await register_and_login(client, "other@example.com")
    _limits(app, rate_limit_api_burst=2, rate_limit_api_per_second=SLOW)

    for _ in range(2):
        await client.get("/v1/endpoints", headers=auth_headers)
    assert (await client.get("/v1/endpoints", headers=auth_headers)).status_code == 429
    assert (await client.get("/v1/endpoints", headers=other)).status_code == 200


async def test_jwt_and_api_key_share_one_budget_per_user(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Otherwise a user could double their quota by alternating credentials."""
    _limits(app, rate_limit_api_burst=3, rate_limit_api_per_second=SLOW)
    created = await client.post("/v1/api-keys", json={"name": "ci"}, headers=auth_headers)  # 1
    machine = {"X-API-Key": created.json()["key"]}

    assert (await client.get("/v1/endpoints", headers=machine)).status_code == 200  # 2
    assert (await client.get("/v1/endpoints", headers=auth_headers)).status_code == 200  # 3
    assert (await client.get("/v1/endpoints", headers=machine)).status_code == 429
    assert (await client.get("/v1/endpoints", headers=auth_headers)).status_code == 429


async def test_ingestion_and_management_api_have_separate_budgets(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _limits(
        app,
        rate_limit_api_burst=1,
        rate_limit_api_per_second=SLOW,
        rate_limit_ingest_burst=2,
        rate_limit_ingest_per_second=SLOW,
    )
    event = {"type": "a.b", "payload": {}}

    assert (await client.get("/v1/endpoints", headers=auth_headers)).status_code == 200
    assert (await client.get("/v1/endpoints", headers=auth_headers)).status_code == 429  # api spent
    assert (await client.post("/v1/events", json=event, headers=auth_headers)).status_code == 202
    assert (await client.post("/v1/events", json=event, headers=auth_headers)).status_code == 202
    assert (await client.post("/v1/events", json=event, headers=auth_headers)).status_code == 429


async def test_a_rejected_ingest_is_not_stored(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _limits(app, rate_limit_ingest_burst=1, rate_limit_ingest_per_second=SLOW)
    event = {"type": "a.b", "payload": {}}
    await client.post("/v1/events", json=event, headers=auth_headers)
    assert (await client.post("/v1/events", json=event, headers=auth_headers)).status_code == 429
    assert len((await client.get("/v1/events", headers=auth_headers)).json()) == 1


async def test_unauthenticated_requests_cannot_drain_someone_elses_budget(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """API key prefixes are semi-public. Limiting by credential *before* auth would let an
    attacker lock a victim out with bogus keys that merely carry the victim's prefix."""
    _limits(app, rate_limit_api_burst=3, rate_limit_api_per_second=SLOW)
    created = await client.post("/v1/api-keys", json={"name": "ci"}, headers=auth_headers)  # 1
    victim_key = created.json()["key"]
    forged = {"X-API-Key": victim_key[: victim_key.rindex("_")] + "_forged-secret"}

    for _ in range(10):
        assert (await client.get("/v1/endpoints", headers=forged)).status_code == 401

    victim = {"X-API-Key": victim_key}
    assert (await client.get("/v1/endpoints", headers=victim)).status_code == 200  # 2
    assert (await client.get("/v1/endpoints", headers=victim)).status_code == 200  # 3
    assert (await client.get("/v1/endpoints", headers=victim)).status_code == 429


# ------------------------------------------------------------ credential endpoints


async def test_login_is_limited_per_ip_even_with_correct_credentials(
    app: FastAPI, client: AsyncClient
) -> None:
    _limits(app, rate_limit_auth_ip_burst=3, rate_limit_auth_ip_refill_seconds=100_000)
    await client.post("/v1/auth/register", json=CREDS)  # 1
    wrong = {**CREDS, "password": "wrong-password-1"}
    assert (await client.post("/v1/auth/login", json=wrong)).status_code == 401  # 2
    assert (await client.post("/v1/auth/login", json=wrong)).status_code == 401  # 3

    blocked = await client.post("/v1/auth/login", json=CREDS)  # right password, still blocked
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": "x"})).status_code == 429


async def test_login_is_limited_per_account_across_ips(app: FastAPI, client: AsyncClient) -> None:
    """A botnet rotating IPs still runs into the per-account limit."""
    _limits(
        app,
        rate_limit_auth_ip_burst=1000,
        rate_limit_login_email_burst=2,
        rate_limit_login_email_refill_seconds=100_000,
    )
    wrong = {"email": "victim@example.com", "password": "guess-number-1"}
    assert (await client.post("/v1/auth/login", json=wrong)).status_code == 401
    assert (await client.post("/v1/auth/login", json=wrong)).status_code == 401
    assert (await client.post("/v1/auth/login", json=wrong)).status_code == 429
    shouty = {**wrong, "email": "VICTIM@Example.com"}
    assert (await client.post("/v1/auth/login", json=shouty)).status_code == 429  # same account
    elsewhere = {"email": "someone-else@example.com", "password": "guess-number-1"}
    assert (await client.post("/v1/auth/login", json=elsewhere)).status_code == 401


async def test_forwarded_for_is_ignored_unless_proxies_are_trusted(
    app: FastAPI, client: AsyncClient
) -> None:
    _limits(
        app,
        rate_limit_auth_ip_burst=2,
        rate_limit_auth_ip_refill_seconds=100_000,
        trusted_proxy_count=0,
    )
    body = {"refresh_token": "x"}
    for n in range(2):
        spoofed = {"X-Forwarded-For": f"10.9.9.{n}"}
        assert (
            await client.post("/v1/auth/refresh", json=body, headers=spoofed)
        ).status_code == 401
    spoofed = {"X-Forwarded-For": "10.9.9.99"}
    assert (await client.post("/v1/auth/refresh", json=body, headers=spoofed)).status_code == 429


async def test_trusted_proxy_uses_the_address_it_appended_not_the_spoofable_prefix(
    app: FastAPI, client: AsyncClient
) -> None:
    _limits(
        app,
        rate_limit_auth_ip_burst=2,
        rate_limit_auth_ip_refill_seconds=100_000,
        trusted_proxy_count=1,
    )

    async def refresh(forwarded_for: str) -> int:
        response = await client.post(
            "/v1/auth/refresh",
            json={"refresh_token": "x"},
            headers={"X-Forwarded-For": forwarded_for},
        )
        return response.status_code

    assert [await refresh("1.1.1.1") for _ in range(2)] == [401, 401]
    assert await refresh("1.1.1.1") == 429
    assert await refresh("2.2.2.2") == 401  # a different real client is unaffected
    # the attacker prepends a fake address; the proxy still appends the real one last
    assert await refresh("8.8.8.8, 1.1.1.1") == 429


# ---------------------------------------------------------------- switches & failure


async def test_rate_limiting_can_be_switched_off(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _limits(app, rate_limit_enabled=False, rate_limit_api_burst=1, rate_limit_api_per_second=SLOW)
    for _ in range(5):
        response = await client.get("/v1/endpoints", headers=auth_headers)
        assert response.status_code == 200
        assert "RateLimit-Limit" not in response.headers


class _BrokenLimiter:
    async def consume(self, *args: object, **kwargs: object) -> None:
        raise RedisConnectionError("redis is down")


async def test_fails_open_when_redis_is_down_by_default(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    before = REGISTRY.get_sample_value("hookhaven_rate_limiter_errors_total") or 0.0
    app.dependency_overrides[get_rate_limiter] = lambda: _BrokenLimiter()

    assert (await client.get("/v1/endpoints", headers=auth_headers)).status_code == 200
    assert (REGISTRY.get_sample_value("hookhaven_rate_limiter_errors_total") or 0.0) == before + 1


async def test_can_be_configured_to_fail_closed(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _limits(app, rate_limit_fail_open=False)
    app.dependency_overrides[get_rate_limiter] = lambda: _BrokenLimiter()
    assert (await client.get("/v1/endpoints", headers=auth_headers)).status_code == 503


@pytest.mark.parametrize("path", ["/health", "/ready"])
async def test_health_endpoints_are_never_rate_limited(
    app: FastAPI, client: AsyncClient, path: str
) -> None:
    _limits(app, rate_limit_api_burst=1, rate_limit_auth_ip_burst=1)
    for _ in range(5):
        assert (await client.get(path)).status_code in (200, 503)
