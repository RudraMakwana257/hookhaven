import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_settings
from app.core.config import Settings
from app.core.crypto import decrypt_secret
from app.models import Endpoint

from .conftest import register_and_login

URL = "https://hooks.example.com/webhooks"


async def _create(client: AsyncClient, headers: dict[str, str], **body: object):
    return await client.post("/v1/endpoints", json={"url": URL, **body}, headers=headers)


async def test_create_returns_secret_once_with_defaults(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await _create(client, auth_headers)
    assert created.status_code == 201
    body = created.json()
    assert body["secret"].startswith("whsec_")
    assert body["event_types"] == ["*"]
    assert body["is_active"] is True

    listed = await client.get("/v1/endpoints", headers=auth_headers)
    assert body["secret"] not in listed.text
    fetched = await client.get(f"/v1/endpoints/{body['id']}", headers=auth_headers)
    assert "secret" not in fetched.json()


async def test_secret_is_encrypted_at_rest(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    secret = (await _create(client, auth_headers)).json()["secret"]
    async with session_maker() as session:
        row = (await session.execute(select(Endpoint))).scalar_one()
    assert secret not in row.secret_encrypted
    assert decrypt_secret(row.secret_encrypted) == secret


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "https://localhost/hook",
        "https://internal.example/hook",
        "https://metadata.example/hook",
        "https://mixed.example/hook",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://hooks.example.com/hook",
        "https://user:pw@hooks.example.com/hook",
        "https://hooks.example.com:22/hook",
    ],
)
async def test_unsafe_destinations_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str], url: str
) -> None:
    response = await _create(client, auth_headers, url=url)
    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]
    assert (await client.get("/v1/endpoints", headers=auth_headers)).json() == []


async def test_event_types_are_validated_and_normalised(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    dedup = await _create(client, auth_headers, event_types=["order.created", "order.created"])
    assert dedup.json()["event_types"] == ["order.created"]
    wildcard = await _create(client, auth_headers, event_types=["order.created", "*"])
    assert wildcard.json()["event_types"] == ["*"]
    for bad in (["Order.Created"], [], ["has space"], ["a" * 65]):
        assert (await _create(client, auth_headers, event_types=bad)).status_code == 422


async def test_patch_updates_only_given_fields(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (await _create(client, auth_headers, description="old")).json()
    patched = await client.patch(
        f"/v1/endpoints/{created['id']}",
        json={"is_active": False, "event_types": ["invoice.paid"]},
        headers=auth_headers,
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["is_active"] is False
    assert body["event_types"] == ["invoice.paid"]
    assert body["description"] == "old"
    assert body["url"] == URL

    cleared = await client.patch(
        f"/v1/endpoints/{created['id']}", json={"description": None}, headers=auth_headers
    )
    assert cleared.json()["description"] is None


async def test_patch_revalidates_url_and_rejects_nulls(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (await _create(client, auth_headers)).json()
    url = f"/v1/endpoints/{created['id']}"
    bad = await client.patch(url, json={"url": "http://10.0.0.1/x"}, headers=auth_headers)
    assert bad.status_code == 422
    assert (await client.get(url, headers=auth_headers)).json()["url"] == URL  # unchanged
    assert (await client.patch(url, json={"url": None}, headers=auth_headers)).status_code == 422


async def test_delete(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = (await _create(client, auth_headers)).json()
    url = f"/v1/endpoints/{created['id']}"
    assert (await client.delete(url, headers=auth_headers)).status_code == 204
    assert (await client.get(url, headers=auth_headers)).status_code == 404


async def test_rotate_secret(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    created = (await _create(client, auth_headers)).json()
    rotated = await client.post(
        f"/v1/endpoints/{created['id']}/rotate-secret", headers=auth_headers
    )
    assert rotated.status_code == 200
    new_secret = rotated.json()["secret"]
    assert new_secret != created["secret"]
    async with session_maker() as session:
        row = (await session.execute(select(Endpoint))).scalar_one()
    assert decrypt_secret(row.secret_encrypted) == new_secret


async def test_users_cannot_see_or_touch_each_others_endpoints(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (await _create(client, auth_headers)).json()
    other = await register_and_login(client, "other@example.com")
    url = f"/v1/endpoints/{created['id']}"

    assert (await client.get("/v1/endpoints", headers=other)).json() == []
    assert (await client.get(url, headers=other)).status_code == 404
    assert (await client.patch(url, json={"is_active": False}, headers=other)).status_code == 404
    assert (await client.delete(url, headers=other)).status_code == 404
    assert (await client.post(f"{url}/rotate-secret", headers=other)).status_code == 404
    assert (await client.get(url, headers=auth_headers)).json()["is_active"] is True


async def test_endpoint_quota(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, max_endpoints_per_user=2
    )
    assert (await _create(client, auth_headers)).status_code == 201
    assert (await _create(client, auth_headers)).status_code == 201
    assert (await _create(client, auth_headers)).status_code == 409


async def test_pagination(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    for i in range(3):
        await _create(client, auth_headers, description=f"e{i}")
    page = await client.get("/v1/endpoints?limit=2", headers=auth_headers)
    rest = await client.get("/v1/endpoints?limit=2&offset=2", headers=auth_headers)
    assert len(page.json()) == 2
    assert len(rest.json()) == 1
    assert (await client.get("/v1/endpoints?limit=0", headers=auth_headers)).status_code == 422


async def test_api_key_can_manage_endpoints_but_not_api_keys(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    key = (await client.post("/v1/api-keys", json={"name": "ci"}, headers=auth_headers)).json()[
        "key"
    ]
    machine = {"X-API-Key": key}

    assert (await _create(client, machine)).status_code == 201
    assert len((await client.get("/v1/endpoints", headers=machine)).json()) == 1
    # Key management stays dashboard-only: a leaked API key cannot mint more keys.
    assert (
        await client.post("/v1/api-keys", json={"name": "x"}, headers=machine)
    ).status_code == 401


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/v1/endpoints")).status_code == 401
    assert (await client.post("/v1/endpoints", json={"url": URL})).status_code == 401
    bad_key = await client.get("/v1/endpoints", headers={"X-API-Key": "hh_deadbeef_nope"})
    assert bad_key.status_code == 401
