from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.api.limits import (
    LimiterDep,
    email_identity,
    enforce,
    limit_auth_ip,
    login_email_policy,
)
from app.core.errors import EmailAlreadyRegisteredError, InvalidCredentialsError, InvalidTokenError
from app.core.security import create_token, decode_token
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenPair, UserOut
from app.services import users

router = APIRouter(prefix="/auth", tags=["auth"])
ip_limited = [Depends(limit_auth_ip)]


def _issue_tokens(user_id: UUID) -> TokenPair:
    return TokenPair(
        access_token=create_token(user_id, "access"),
        refresh_token=create_token(user_id, "refresh"),
    )


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=ip_limited,
)
async def register(body: RegisterRequest, session: SessionDep) -> UserOut:
    try:
        user = await users.register_user(session, body.email, body.password)
    except EmailAlreadyRegisteredError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenPair, dependencies=ip_limited)
async def login(
    body: LoginRequest,
    session: SessionDep,
    response: Response,
    limiter: LimiterDep,
    settings: SettingsDep,
) -> TokenPair:
    # Per-account limit on top of the per-IP one: a botnet spreading guesses over many IPs
    # still hits this. Checked before hashing so blocked attempts cost no CPU.
    await enforce(
        limiter=limiter,
        settings=settings,
        response=response,
        scope="login-email",
        identity=email_identity(body.email),
        policy=login_email_policy(settings),
    )
    try:
        user = await users.authenticate_user(session, body.email, body.password)
    except InvalidCredentialsError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password") from None
    return _issue_tokens(user.id)


@router.post("/refresh", response_model=TokenPair, dependencies=ip_limited)
async def refresh(body: RefreshRequest, session: SessionDep) -> TokenPair:
    try:
        user_id = decode_token(body.refresh_token, "refresh")
    except InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from None
    if await users.get_user_by_id(session, user_id) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    return _issue_tokens(user_id)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
