from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient

from app.api.deps import get_api_key_user
from app.models import User

from .conftest import register_and_login


@pytest.fixture(autouse=True)
def _api_key_protected_route(app: FastAPI) -> Iterator[None]:
    """Stand-in for the real API-key-protected routes arriving in phase 4."""

    @app.get("/_whoami")
    async def whoami(user: Annotated[User, Depends(get_api_key_user)]) -> dict[str, str]:
        return {"email": user.email}

    yield


async def _create_key(client: AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    response = await client.post("/v1/api-keys", json={"name": "ci"}, headers=headers)
    assert response.status_code == 201
    return dict(response.json())


async def test_key_is_shown_once_and_never_listed(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await _create_key(client, auth_headers)
    assert created["key"].startswith("hh_")

    listed = await client.get("/v1/api-keys", headers=auth_headers)
    assert listed.status_code == 200
    assert created["key"] not in listed.text
    assert listed.json()[0]["prefix"] == created["prefix"]


async def test_api_key_authenticates_requests(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await _create_key(client, auth_headers)
    ok = await client.get("/_whoami", headers={"X-API-Key": created["key"]})
    assert ok.status_code == 200
    assert ok.json() == {"email": "owner@example.com"}

    listed = await client.get("/v1/api-keys", headers=auth_headers)
    assert listed.json()[0]["last_used_at"] is not None


@pytest.mark.parametrize("header", [None, "", "garbage", "hh_deadbeef_wrong"])
async def test_missing_or_invalid_key_is_401(client: AsyncClient, header: str | None) -> None:
    headers = {"X-API-Key": header} if header is not None else {}
    assert (await client.get("/_whoami", headers=headers)).status_code == 401


async def test_revoked_key_stops_working(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await _create_key(client, auth_headers)
    revoked = await client.delete(f"/v1/api-keys/{created['id']}", headers=auth_headers)
    assert revoked.status_code == 204
    assert (await client.get("/_whoami", headers={"X-API-Key": created["key"]})).status_code == 401


async def test_cannot_revoke_someone_elses_key(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await _create_key(client, auth_headers)
    other = await register_and_login(client, "other@example.com")
    response = await client.delete(f"/v1/api-keys/{created['id']}", headers=other)
    assert response.status_code == 404
    assert (await client.get("/_whoami", headers={"X-API-Key": created["key"]})).status_code == 200


async def test_api_key_endpoints_require_login(client: AsyncClient) -> None:
    assert (await client.get("/v1/api-keys")).status_code == 401
    assert (await client.post("/v1/api-keys", json={"name": "x"})).status_code == 401
