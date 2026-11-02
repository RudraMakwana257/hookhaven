import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EmailAlreadyRegisteredError, InvalidCredentialsError
from app.core.security import burn_password_check, hash_password, verify_password
from app.models import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def register_user(session: AsyncSession, email: str, password: str) -> User:
    # argon2 is CPU-bound: keep it off the event loop.
    password_hash = await asyncio.to_thread(hash_password, password)
    user = User(email=normalize_email(email), password_hash=password_hash)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise EmailAlreadyRegisteredError(email) from None
    return user


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User:
    user = await get_user_by_email(session, email)
    if user is None:
        await asyncio.to_thread(burn_password_check, password)
        raise InvalidCredentialsError
    if not await asyncio.to_thread(verify_password, password, user.password_hash):
        raise InvalidCredentialsError
    return user
