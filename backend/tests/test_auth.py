from httpx import AsyncClient

CREDS = {"email": "dev@example.com", "password": "correct-horse-1"}


async def test_register_then_login_then_me(client: AsyncClient) -> None:
    registered = await client.post("/v1/auth/register", json=CREDS)
    assert registered.status_code == 201
    assert "password" not in registered.text

    login = await client.post("/v1/auth/login", json=CREDS)
    assert login.status_code == 200
    tokens = login.json()

    me = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == CREDS["email"]


async def test_duplicate_email_is_case_insensitive_conflict(client: AsyncClient) -> None:
    await client.post("/v1/auth/register", json=CREDS)
    again = await client.post("/v1/auth/register", json={**CREDS, "email": "DEV@Example.com"})
    assert again.status_code == 409


async def test_weak_password_and_bad_email_rejected(client: AsyncClient) -> None:
    assert (
        await client.post("/v1/auth/register", json={**CREDS, "password": "short"})
    ).status_code == 422
    assert (
        await client.post("/v1/auth/register", json={**CREDS, "email": "nope"})
    ).status_code == 422


async def test_login_failures_are_indistinguishable(client: AsyncClient) -> None:
    await client.post("/v1/auth/register", json=CREDS)
    wrong_password = await client.post("/v1/auth/login", json={**CREDS, "password": "bad-pass-1"})
    unknown_user = await client.post(
        "/v1/auth/login", json={"email": "ghost@example.com", "password": "bad-pass-1"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


async def test_me_requires_valid_access_token(client: AsyncClient) -> None:
    assert (await client.get("/v1/auth/me")).status_code == 401
    bad = await client.get("/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert bad.status_code == 401


async def test_refresh_flow(client: AsyncClient) -> None:
    await client.post("/v1/auth/register", json=CREDS)
    tokens = (await client.post("/v1/auth/login", json=CREDS)).json()

    refreshed = await client.post(
        "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200

    # An access token must not work as a refresh token, and vice versa.
    as_refresh = await client.post(
        "/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert as_refresh.status_code == 401
    as_access = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )
    assert as_access.status_code == 401
