import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.core.errors import EndpointLimitReachedError
from app.core.security import generate_webhook_secret
from app.core.ssrf import DestinationValidator
from app.models import Endpoint, User

_UPDATABLE_FIELDS = frozenset({"url", "description", "event_types", "is_active"})


async def create_endpoint(
    session: AsyncSession,
    user: User,
    *,
    url: str,
    description: str | None,
    event_types: list[str],
    validator: DestinationValidator,
    max_endpoints: int,
) -> tuple[Endpoint, str]:
    await validator(url)  # raises UnsafeUrlError

    count = await session.scalar(
        select(func.count()).select_from(Endpoint).where(Endpoint.user_id == user.id)
    )
    if (count or 0) >= max_endpoints:
        raise EndpointLimitReachedError

    secret = generate_webhook_secret()
    endpoint = Endpoint(
        user_id=user.id,
        url=url,
        description=description,
        event_types=event_types,
        secret_encrypted=encrypt_secret(secret),
    )
    session.add(endpoint)
    await session.commit()
    return endpoint, secret


async def list_endpoints(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int, offset: int
) -> list[Endpoint]:
    result = await session.execute(
        select(Endpoint)
        .where(Endpoint.user_id == user_id)
        .order_by(Endpoint.created_at.desc(), Endpoint.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars())


async def get_endpoint(
    session: AsyncSession, user_id: uuid.UUID, endpoint_id: uuid.UUID
) -> Endpoint | None:
    """Ownership is part of the query: other users' endpoints look like they don't exist."""
    result = await session.execute(
        select(Endpoint).where(Endpoint.id == endpoint_id, Endpoint.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def update_endpoint(
    session: AsyncSession,
    endpoint: Endpoint,
    changes: dict[str, Any],
    validator: DestinationValidator,
) -> Endpoint:
    if "url" in changes:
        await validator(changes["url"])  # re-validate: a URL change is a new SSRF decision
    for field, value in changes.items():
        if field in _UPDATABLE_FIELDS:
            setattr(endpoint, field, value)
    await session.commit()
    await session.refresh(endpoint)  # pick up the server-side `updated_at`
    return endpoint


async def rotate_secret(session: AsyncSession, endpoint: Endpoint) -> str:
    secret = generate_webhook_secret()
    endpoint.secret_encrypted = encrypt_secret(secret)
    await session.commit()
    await session.refresh(endpoint)
    return secret


async def delete_endpoint(session: AsyncSession, endpoint: Endpoint) -> None:
    await session.delete(endpoint)
    await session.commit()
