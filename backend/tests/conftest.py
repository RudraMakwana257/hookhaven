from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import create_app
from app.models import Base


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    """A fresh app wired to an isolated in-memory database."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_session] = override_session
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Registers a user and returns Bearer headers for them."""
    return await register_and_login(client, "owner@example.com")


async def register_and_login(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/v1/auth/register", json={"email": email, "password": "correct-horse-1"})
    response = await client.post(
        "/v1/auth/login", json={"email": email, "password": "correct-horse-1"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
