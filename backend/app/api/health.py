from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.readiness import Check, get_readiness_checks

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Liveness: the process is up. Never touches dependencies."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(
    response: Response,
    checks: Annotated[dict[str, Check], Depends(get_readiness_checks)],
) -> dict[str, object]:
    """Readiness: dependencies are reachable. 503 if any check fails."""
    results = {name: await check() for name, check in checks.items()}
    healthy = all(results.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": results}
