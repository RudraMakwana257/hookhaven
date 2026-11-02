from collections.abc import Awaitable, Callable

from sqlalchemy import text

from app.core.redis import get_redis
from app.db.session import get_engine

Check = Callable[[], Awaitable[bool]]


async def check_database() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def check_redis() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


def get_readiness_checks() -> dict[str, Check]:
    """Dependency so tests can swap real infrastructure checks for fakes."""
    return {"database": check_database, "redis": check_redis}
