from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.api_keys import router as api_keys_router
from app.api.auth import router as auth_router
from app.api.deliveries import router as deliveries_router
from app.api.endpoints import router as endpoints_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.metrics import BUILD_INFO
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.core.redis import close_redis
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await close_redis()
    await dispose_engine()


async def unhandled_exception_handler(_: Request, __: Exception) -> JSONResponse:
    """Never leak internals; give the client an id that matches our logs."""
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
        headers={REQUEST_ID_HEADER: str(request_id)} if request_id else None,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "development")

    app = FastAPI(
        title="Hookhaven",
        version="0.1.0",
        description="Webhook delivery and event reliability platform.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    BUILD_INFO.labels(app.version).set(1)
    app.include_router(health_router)
    app.include_router(metrics_router)

    v1 = APIRouter(prefix="/v1")
    v1.include_router(auth_router)
    v1.include_router(api_keys_router)
    v1.include_router(endpoints_router)
    v1.include_router(events_router)
    v1.include_router(deliveries_router)
    app.include_router(v1)
    return app


app = create_app()
