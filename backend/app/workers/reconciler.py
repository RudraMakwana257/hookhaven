"""Reconciler: heals the gap between Postgres (truth) and Redis (transport).

Postgres says a delivery is `pending` and due, but the queue may have lost track of it because:
  * Redis was down or wiped (restart without persistence, failover, flush),
  * the process crashed between COMMIT and enqueue at ingestion or replay,
  * a worker died and its in-memory state is gone.

A healthy pending delivery is processed within seconds of becoming due, so one that is overdue by
more than a grace period is suspect. We push it back onto the queue. That is always safe: the
worker's compare-and-set reservation makes duplicate queue entries harmless.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.time import utcnow
from app.models import Delivery, DeliveryStatus
from app.queue.delivery_queue import DeliveryQueue

log = structlog.get_logger(__name__)


class Reconciler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue: DeliveryQueue,
        settings: Settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._settings = settings
        self._clock = clock

    async def sweep(self) -> int:
        """Re-enqueue overdue pending deliveries. Returns how many were pushed."""
        cutoff = self._clock() - timedelta(seconds=self._settings.reconcile_grace_seconds)
        async with self._session_factory() as session:
            overdue = list(
                (
                    await session.execute(
                        select(Delivery.id)
                        .where(
                            Delivery.status == DeliveryStatus.PENDING.value,
                            or_(
                                Delivery.next_attempt_at.is_(None),
                                Delivery.next_attempt_at <= cutoff,
                            ),
                        )
                        .order_by(Delivery.next_attempt_at)
                        .limit(self._settings.reconcile_batch_size)
                    )
                ).scalars()
            )
        if not overdue:
            return 0
        # Marker TTL = grace: while a backlog drains we do not pile duplicates onto the queue.
        pushed = await self._queue.enqueue_once(
            overdue, dedupe_ttl_seconds=self._settings.reconcile_grace_seconds
        )
        if pushed:
            log.warning(
                "reconciler_requeued_overdue_deliveries", count=pushed, overdue=len(overdue)
            )
        return pushed

    async def sweep_if_leader(self) -> int:
        """Only one worker replica sweeps per interval (Redis lock); the others skip."""
        ttl = max(1, int(self._settings.reconcile_interval_seconds * 0.9))
        if not await self._queue.acquire_lock("reconciler", ttl_seconds=ttl):
            return 0
        return await self.sweep()
