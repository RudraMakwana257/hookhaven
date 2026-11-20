import hmac
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionDep, SettingsDep, get_queue_inspector
from app.core.metrics import DELIVERY_LAG, QUEUE_DEPTH, SCRAPE_ERRORS
from app.core.time import as_utc, utcnow
from app.models import Delivery, DeliveryStatus
from app.queue.delivery_queue import DeliveryQueue

log = structlog.get_logger(__name__)
router = APIRouter(include_in_schema=False)


@router.get("/metrics")
async def metrics(
    session: SessionDep,
    settings: SettingsDep,
    inspector: Annotated[DeliveryQueue, Depends(get_queue_inspector)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if settings.metrics_token:
        expected = f"Bearer {settings.metrics_token}"
        if not hmac.compare_digest((authorization or "").encode(), expected.encode()):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Unauthorized", headers={"WWW-Authenticate": "Bearer"}
            )

    # Gauges that describe *current state* are computed at scrape time. If a dependency is down we
    # still serve everything else, and count the failure: a scrape must not fail because Redis did.
    try:
        for state, depth in (await inspector.stats()).items():
            QUEUE_DEPTH.labels(state).set(depth)
    except (RedisError, OSError):
        SCRAPE_ERRORS.labels("redis").inc()
        log.warning("metrics_redis_unavailable", exc_info=True)

    try:
        now = utcnow()
        oldest = await session.scalar(
            select(func.min(Delivery.next_attempt_at)).where(
                Delivery.status == DeliveryStatus.PENDING.value, Delivery.next_attempt_at <= now
            )
        )
        DELIVERY_LAG.set(0 if oldest is None else max(0.0, (now - as_utc(oldest)).total_seconds()))
    except SQLAlchemyError:
        SCRAPE_ERRORS.labels("database").inc()
        log.warning("metrics_database_unavailable", exc_info=True)

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
