import ipaddress
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_delivery_queue, get_destination_validator
from app.core.ssrf import DEFAULT_ALLOWED_PORTS, DestinationValidator, UrlPolicy
from app.db.session import get_session
from app.main import create_app
from app.models import Base

from .fakes import FakeQueue

PUBLIC_IP = "93.184.216.34"
FAKE_DNS: dict[str, list[str]] = {
    "localhost": ["127.0.0.1"],
    "internal.example": ["10.0.0.5"],
    "metadata.example": ["169.254.169.254"],
    "mixed.example": [PUBLIC_IP, "10.0.0.5"],
    "nxdomain.example": [],
}


async def fake_resolver(host: str, port: int) -> list[str]:
    """Fake DNS: IP literals resolve to themselves, unknown names are public."""
    if host in FAKE_DNS:
        return FAKE_DNS[host]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return [PUBLIC_IP]
    return [host]


def policy_for_tests() -> UrlPolicy:
    return UrlPolicy(
        allowed_schemes=frozenset({"http", "https"}),
        allowed_ports=DEFAULT_ALLOWED_PORTS,
        allow_private=False,
    )


@pytest.fixture
async def session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def app(session_maker: async_sessionmaker[AsyncSession], queue: FakeQueue) -> FastAPI:
    """A fresh app wired to an isolated in-memory database and a fake DNS resolver."""

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_destination_validator] = lambda: DestinationValidator(
        policy_for_tests(), fake_resolver
    )
    application.dependency_overrides[get_delivery_queue] = lambda: queue
    return application


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
