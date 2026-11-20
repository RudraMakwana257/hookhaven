"""Server-Sent Events framing and the stream loop."""

import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from app.realtime.bus import Subscription

HEARTBEAT = ": keep-alive\n\n"  # a comment line: keeps proxies/load balancers from idling us out
_SAFE_EVENT_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}")


def format_sse(event: str, data: Mapping[str, Any]) -> str:
    # Event names come from our own publishers, but never let one inject a newline into a frame.
    name = event if _SAFE_EVENT_NAME.fullmatch(event) else "message"
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream(
    subscription: Subscription,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_seconds: float,
    max_lifetime_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[str]:
    """Yield SSE frames until the client leaves or the lifetime cap is reached.

    The lifetime cap exists because a stream is authenticated once, at connect time: closing it
    forces the client to reconnect with a fresh token (it gets a `reconnect` event first).
    """
    started = last_sent = clock()
    yield format_sse("ready", {})
    while True:
        if clock() - started >= max_lifetime_seconds:
            yield format_sse("reconnect", {"reason": "max_lifetime"})
            return
        if await is_disconnected():
            return
        message = await subscription.next_message(wait_seconds=min(1.0, heartbeat_seconds))
        if message is not None:
            yield format_sse(str(message.get("type", "message")), message)
            last_sent = clock()
        elif clock() - last_sent >= heartbeat_seconds:
            yield HEARTBEAT
            last_sent = clock()
