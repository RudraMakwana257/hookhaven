"""FastAPI dependencies that apply rate limits.

Authenticated limits are keyed by *user id after authentication*. Keying by the raw credential
(or its public prefix) before auth would let anyone drain a victim's bucket by sending requests
with the victim's key prefix.
"""

import hashlib
import math
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, Response, status
from redis.exceptions import RedisError

from app.api.deps import AuthenticatedUser, CurrentUser, SettingsDep
from app.core.config import Settings
from app.core.metrics import RATE_LIMITED, RATE_LIMITER_ERRORS
from app.core.rate_limit import RateLimiter, RateLimitPolicy
from app.core.redis import get_redis
from app.core.time import utcnow

log = structlog.get_logger(__name__)


def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_redis())


LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def client_ip(request: Request, trusted_proxies: int) -> str:
    """The caller's IP. X-Forwarded-For is attacker-controlled unless we know how many trusted
    proxies append to it, so it is ignored by default; with N trusted proxies we take the Nth
    entry from the right, which an attacker cannot forge."""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxies <= 0:
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    return hops[-trusted_proxies] if len(hops) >= trusted_proxies else peer


def api_policy(s: Settings) -> RateLimitPolicy:
    return RateLimitPolicy(s.rate_limit_api_burst, s.rate_limit_api_per_second)


def ingest_policy(s: Settings) -> RateLimitPolicy:
    return RateLimitPolicy(s.rate_limit_ingest_burst, s.rate_limit_ingest_per_second)


def auth_ip_policy(s: Settings) -> RateLimitPolicy:
    return RateLimitPolicy(s.rate_limit_auth_ip_burst, 1 / s.rate_limit_auth_ip_refill_seconds)


def login_email_policy(s: Settings) -> RateLimitPolicy:
    return RateLimitPolicy(
        s.rate_limit_login_email_burst, 1 / s.rate_limit_login_email_refill_seconds
    )


async def enforce(
    *,
    limiter: RateLimiter,
    settings: Settings,
    response: Response,
    scope: str,
    identity: str,
    policy: RateLimitPolicy,
) -> None:
    """Spend a token or raise 429. Sets RateLimit-* headers on success."""
    if not settings.rate_limit_enabled:
        return
    try:
        result = await limiter.consume(scope, identity, policy, now=utcnow().timestamp())
    except (RedisError, OSError):
        RATE_LIMITER_ERRORS.inc()
        log.warning("rate_limiter_unavailable", scope=scope, exc_info=True)
        if settings.rate_limit_fail_open:
            return  # availability over strictness; the metric above makes it visible
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Rate limiter unavailable"
        ) from None

    if not result.allowed:
        RATE_LIMITED.labels(scope).inc()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded",
            headers={
                "Retry-After": str(max(1, math.ceil(result.retry_after_seconds))),
                "RateLimit-Limit": str(result.limit),
                "RateLimit-Remaining": "0",
            },
        )
    response.headers["RateLimit-Limit"] = str(result.limit)
    response.headers["RateLimit-Remaining"] = str(result.remaining)


async def limit_api(
    user: AuthenticatedUser, response: Response, limiter: LimiterDep, settings: SettingsDep
) -> None:
    await enforce(
        limiter=limiter,
        settings=settings,
        response=response,
        scope="api",
        identity=str(user.id),
        policy=api_policy(settings),
    )


async def limit_dashboard(
    user: CurrentUser, response: Response, limiter: LimiterDep, settings: SettingsDep
) -> None:
    await enforce(
        limiter=limiter,
        settings=settings,
        response=response,
        scope="api",  # same bucket as limit_api: one user, one budget
        identity=str(user.id),
        policy=api_policy(settings),
    )


async def limit_ingest(
    user: AuthenticatedUser, response: Response, limiter: LimiterDep, settings: SettingsDep
) -> None:
    """Event ingestion has its own, larger bucket so heavy producers cannot lock themselves out
    of the management API (and vice versa)."""
    await enforce(
        limiter=limiter,
        settings=settings,
        response=response,
        scope="ingest",
        identity=str(user.id),
        policy=ingest_policy(settings),
    )


async def limit_auth_ip(
    request: Request, response: Response, limiter: LimiterDep, settings: SettingsDep
) -> None:
    """Credential endpoints, per client IP. Runs before any password hashing happens."""
    await enforce(
        limiter=limiter,
        settings=settings,
        response=response,
        scope="auth-ip",
        identity=client_ip(request, settings.trusted_proxy_count),
        policy=auth_ip_policy(settings),
    )


def email_identity(email: str) -> str:
    """Hash so that no addresses end up in Redis keys."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]
