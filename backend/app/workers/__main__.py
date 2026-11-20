"""Entry point:  python -m app.workers"""

import asyncio
import signal

import structlog
from prometheus_client import start_http_server

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import close_redis, get_redis
from app.core.ssrf import policy_from_settings
from app.db.session import dispose_engine, get_sessionmaker
from app.queue.delivery_queue import DeliveryQueue
from app.realtime.bus import EventBus
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
        bus=EventBus(get_redis()),
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port)  # scraped on the internal network only
    log.info(
        "worker_started",
        concurrency=settings.worker_concurrency,
        metrics_port=settings.worker_metrics_port,
    )
    try:
        await worker.run(stop)
    finally:
        log.info("worker_stopped")
        await close_redis()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
