"""Entry point:  python -m app.workers"""

import asyncio
import signal

import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import close_redis, get_redis
from app.core.ssrf import policy_from_settings
from app.db.session import dispose_engine, get_sessionmaker
from app.queue.delivery_queue import DeliveryQueue
from app.workers.delivery_worker import Worker


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "development")
    log = structlog.get_logger("worker")

    worker = Worker(
        session_factory=get_sessionmaker(),
        queue=DeliveryQueue(get_redis()),
        settings=settings,
        policy=policy_from_settings(settings),
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    log.info("worker_started", concurrency=settings.worker_concurrency)
    try:
        await worker.run(stop)
    finally:
        log.info("worker_stopped")
        await close_redis()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
