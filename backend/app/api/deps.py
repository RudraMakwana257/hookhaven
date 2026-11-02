from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import InvalidTokenError
from app.core.security import decode_token
from app.core.ssrf import DestinationValidator, policy_from_settings
from app.db.session import get_session
from app.models import User
from app.services.api_keys import authenticate_api_key
from app.services.users import get_user_by_id

bearer_scheme = HTTPBearer(auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _unauthorized(detail: str, scheme: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": scheme})


async def _user_from_jwt(session: AsyncSession, token: str) -> User:
    try:
        user_id = decode_token(token, "access")
    except InvalidTokenError:
        raise _unauthorized("Invalid or expired token", "Bearer") from None
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise _unauthorized("Invalid or expired token", "Bearer")
    return user


async def _user_from_api_key(session: AsyncSession, raw_key: str) -> User:
    user = await authenticate_api_key(session, raw_key)
    if user is None:
        raise _unauthorized("Invalid API key", "ApiKey")
    return user


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Dashboard-only auth: Bearer JWT access token."""
    if credentials is None:
        raise _unauthorized("Not authenticated", "Bearer")
    return await _user_from_jwt(session, credentials.credentials)


async def get_api_key_user(
    session: SessionDep,
    api_key: Annotated[str | None, Depends(api_key_scheme)],
) -> User:
    """Machine-only auth: `X-API-Key` header."""
    if not api_key:
        raise _unauthorized("Missing API key", "ApiKey")
    return await _user_from_api_key(session, api_key)


async def get_authenticated_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    api_key: Annotated[str | None, Depends(api_key_scheme)],
) -> User:
    """Either credential works. If both are sent, the API key wins."""
    if api_key:
        return await _user_from_api_key(session, api_key)
    if credentials is not None:
        return await _user_from_jwt(session, credentials.credentials)
    raise _unauthorized("Not authenticated", "Bearer")


def get_destination_validator() -> DestinationValidator:
    return DestinationValidator(policy_from_settings(get_settings()))


CurrentUser = Annotated[User, Depends(get_current_user)]
ApiKeyUser = Annotated[User, Depends(get_api_key_user)]
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]
ValidatorDep = Annotated[DestinationValidator, Depends(get_destination_validator)]
