from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api.api_keys import router as api_keys_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.redis import close_redis
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await close_redis()
    await dispose_engine()


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
    app.include_router(health_router)

    v1 = APIRouter(prefix="/v1")
    v1.include_router(auth_router)
    v1.include_router(api_keys_router)
    app.include_router(v1)
    return app


app = create_app()
