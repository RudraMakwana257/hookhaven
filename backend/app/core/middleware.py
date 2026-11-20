import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_UNMATCHED = "unmatched"


def _route_template(request: Request) -> str:
    """`/v1/endpoints/{endpoint_id}`, not `/v1/endpoints/1f3a...`: keeps label cardinality bounded.

    Built from the matched path and its path params rather than `route.path`, because routes only
    know their own prefix-less path when routers are nested. Unknown paths (scanners, typos) all
    collapse into one `unmatched` label.
    """
    if request.scope.get("route") is None:
        return _UNMATCHED
    remaining = {
        name: str(value) for name, value in (request.scope.get("path_params") or {}).items()
    }
    segments = []
    for segment in str(request.scope["path"]).split("/"):
        for name, value in remaining.items():
            if segment == value:
                segments.append("{" + name + "}")
                del remaining[name]
                break
        else:
            segments.append(segment)
    return "/".join(segments)


def _record_http(request: Request, status: int, seconds: float) -> None:
    if request.url.path == "/metrics":
        return
    route = _route_template(request)
    HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
    HTTP_DURATION.labels(request.method, route).observe(seconds)


def _apply_security_headers(request: Request, response: Response) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/v1"):
        response.headers["Cache-Control"] = "no-store"  # tokens, secrets and payloads


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Per request: a request id on every log line, one access log, metrics, security headers."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        # Client-supplied ids are echoed into logs and headers: accept only a safe alphabet.
        request_id = supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        log = structlog.get_logger("http")

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _record_http(request, 500, time.perf_counter() - started)
            log.exception("unhandled_exception", method=request.method, path=request.url.path)
            raise
        elapsed = time.perf_counter() - started

        response.headers[REQUEST_ID_HEADER] = request_id
        _apply_security_headers(request, response)
        _record_http(request, response.status_code, elapsed)

        emit = log.error if response.status_code >= 500 else log.info
        emit(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(elapsed * 1000, 2),
        )
        return response
