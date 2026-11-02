import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import api_key_matches, generate_api_key, parse_api_key_prefix
from app.models import ApiKey, User


async def create_api_key(session: AsyncSession, user: User, name: str) -> tuple[ApiKey, str]:
    generated = generate_api_key()
    api_key = ApiKey(
        user_id=user.id, name=name, prefix=generated.prefix, key_hash=generated.key_hash
    )
    session.add(api_key)
    await session.commit()
    return api_key, generated.full_key


async def list_api_keys(session: AsyncSession, user_id: uuid.UUID) -> list[ApiKey]:
    result = await session.execute(
        select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars())


async def revoke_api_key(session: AsyncSession, user_id: uuid.UUID, key_id: uuid.UUID) -> bool:
    """Revoke a key owned by the user. Returns False if it doesn't exist / isn't theirs."""
    api_key = await session.get(ApiKey, key_id)
    if api_key is None or api_key.user_id != user_id:
        return False
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        await session.commit()
    return True


async def authenticate_api_key(session: AsyncSession, raw_key: str) -> User | None:
    prefix = parse_api_key_prefix(raw_key)
    if prefix is None:
        return None
    result = await session.execute(
        select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
    )
    api_key = result.scalar_one_or_none()
    if api_key is None or not api_key_matches(raw_key, api_key.key_hash):
        return None
    api_key.last_used_at = datetime.now(UTC)
    await session.commit()
    return await session.get(User, api_key.user_id)
