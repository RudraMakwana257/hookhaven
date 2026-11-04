import uuid
from collections.abc import Sequence

import structlog
from redis.exceptions import RedisError

from app.queue.delivery_queue import DeliveryPublisher

log = structlog.get_logger(__name__)


async def publish_best_effort(
    queue: DeliveryPublisher, delivery_ids: Sequence[uuid.UUID], *, reason: str
) -> None:
    """Enqueue AFTER the database commit. A queue outage must not fail work we already stored:
    the reconciler re-enqueues anything that is pending and overdue."""
    try:
        await queue.enqueue(delivery_ids)
    except (RedisError, OSError):
        log.warning(
            "enqueue_failed_will_be_reconciled",
            reason=reason,
            delivery_count=len(delivery_ids),
            exc_info=True,
        )
