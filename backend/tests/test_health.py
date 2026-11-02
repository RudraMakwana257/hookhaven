from httpx import AsyncClient

from app.core.readiness import Check, get_readiness_checks
from app.main import app


def _override(**results: bool) -> None:
    def make(value: bool) -> Check:
        async def check() -> bool:
            return value

        return check

    app.dependency_overrides[get_readiness_checks] = lambda: {
        name: make(value) for name, value in results.items()
    }


async def test_liveness_does_not_need_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_ok_when_all_checks_pass(client: AsyncClient) -> None:
    _override(database=True, redis=True)
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": True, "redis": True}


async def test_readiness_503_when_a_dependency_is_down(client: AsyncClient) -> None:
    _override(database=True, redis=False)
    response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


async def test_request_id_is_echoed_and_generated(client: AsyncClient) -> None:
    echoed = await client.get("/health", headers={"X-Request-ID": "abc123"})
    assert echoed.headers["X-Request-ID"] == "abc123"
    generated = await client.get("/health")
    assert len(generated.headers["X-Request-ID"]) == 32
