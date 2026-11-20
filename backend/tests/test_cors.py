from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.main import create_app

ALLOWED = "https://app.example.com"


@pytest.fixture
def with_origins(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{ALLOWED}, http://localhost:3000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def cors_client(with_origins: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c


async def test_preflight_from_an_allowed_origin_succeeds(cors_client: AsyncClient) -> None:
    response = await cors_client.options(
        "/v1/events",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type, idempotency-key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    assert "idempotency-key" in allowed
    assert "access-control-allow-credentials" not in response.headers  # bearer tokens, no cookies


async def test_preflight_from_an_unknown_origin_is_refused(cors_client: AsyncClient) -> None:
    response = await cors_client.options(
        "/v1/events",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_responses_expose_the_headers_the_dashboard_needs(cors_client: AsyncClient) -> None:
    response = await cors_client.get("/health", headers={"Origin": ALLOWED})
    assert response.headers["access-control-allow-origin"] == ALLOWED
    exposed = response.headers["access-control-expose-headers"].lower()
    for name in ("ratelimit-remaining", "retry-after", "x-request-id", "idempotent-replayed"):
        assert name in exposed


async def test_error_responses_carry_cors_headers_so_the_browser_can_read_them(
    cors_client: AsyncClient,
) -> None:
    response = await cors_client.get("/v1/auth/me", headers={"Origin": ALLOWED})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == ALLOWED


async def test_cors_is_off_by_default(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"Origin": ALLOWED})
    assert "access-control-allow-origin" not in response.headers


def test_production_refuses_a_wildcard_origin() -> None:
    from cryptography.fernet import Fernet

    base = {
        "environment": "production",
        "jwt_secret": "x" * 40,
        "encryption_keys": Fernet.generate_key().decode(),
        "metrics_token": "m" * 32,
    }
    with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS"):
        Settings(_env_file=None, cors_allowed_origins="*", **base)  # type: ignore[arg-type]
    Settings(_env_file=None, cors_allowed_origins=ALLOWED, **base)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["app.example.com", "https://app.example.com/", "ftp://x.example"])
def test_malformed_origins_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="CORS origin"):
        Settings(_env_file=None, cors_allowed_origins=bad)
