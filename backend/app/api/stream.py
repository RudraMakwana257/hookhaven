import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import AuthenticatedUser, BusDep, SettingsDep
from app.api.limits import limit_api
from app.core.security import token_seconds_remaining
from app.realtime.sse import stream

router = APIRouter(prefix="/stream", tags=["realtime"], dependencies=[Depends(limit_api)])

# Open streams per user, for THIS process. A per-replica cap, not a global one: enough to stop one
# user from exhausting Redis connections (each stream holds one), which is the actual concern.
_open_streams: dict[uuid.UUID, int] = {}


def _stream_lifetime(request: Request, configured: float) -> float:
    """Never outlive the credential: a JWT-authenticated stream ends when the token expires."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer ") and not request.headers.get("x-api-key"):
        remaining = token_seconds_remaining(header[7:])
        if remaining is not None:
            return max(0.0, min(configured, remaining))
    return configured


@router.get("", response_class=StreamingResponse)
async def stream_updates(
    request: Request, user: AuthenticatedUser, bus: BusDep, settings: SettingsDep
) -> StreamingResponse:
    """Live updates (`event.created`, `delivery.updated`, `endpoint.disabled`, ...) as SSE.

    EventSource cannot send an Authorization header, so clients use `fetch` streaming. Messages
    are hints, not a durable log: refetch state after every (re)connect.
    """
    user_id = user.id
    if _open_streams.get(user_id, 0) >= settings.sse_max_streams_per_user:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many open streams",
            headers={"Retry-After": "5"},
        )
    lifetime = _stream_lifetime(request, settings.sse_max_lifetime_seconds)

    async def body() -> AsyncIterator[str]:
        _open_streams[user_id] = _open_streams.get(user_id, 0) + 1
        try:
            async with bus.subscribe(user_id) as subscription:
                async for frame in stream(
                    subscription,
                    is_disconnected=request.is_disconnected,
                    heartbeat_seconds=settings.sse_heartbeat_seconds,
                    max_lifetime_seconds=lifetime,
                ):
                    yield frame
        finally:
            _open_streams[user_id] -= 1
            if _open_streams[user_id] <= 0:
                del _open_streams[user_id]

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},  # no proxy buffering
    )
