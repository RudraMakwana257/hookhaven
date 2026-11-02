import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthenticatedUser, SessionDep, SettingsDep, ValidatorDep
from app.core.errors import EndpointLimitReachedError, UnsafeUrlError
from app.models import Endpoint, User
from app.schemas.endpoint import EndpointCreate, EndpointOut, EndpointUpdate, EndpointWithSecret
from app.services import endpoints

router = APIRouter(prefix="/endpoints", tags=["endpoints"])


def _unsafe(exc: UnsafeUrlError) -> HTTPException:
    return HTTPException(422, f"Destination URL not allowed: {exc.reason}")


async def _get_or_404(session: AsyncSession, user: User, endpoint_id: uuid.UUID) -> Endpoint:
    endpoint = await endpoints.get_endpoint(session, user.id, endpoint_id)
    if endpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint not found")
    return endpoint


@router.post("", response_model=EndpointWithSecret, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    body: EndpointCreate,
    user: AuthenticatedUser,
    session: SessionDep,
    validator: ValidatorDep,
    settings: SettingsDep,
) -> EndpointWithSecret:
    try:
        endpoint, secret = await endpoints.create_endpoint(
            session,
            user,
            url=body.url,
            description=body.description,
            event_types=body.event_types,
            validator=validator,
            max_endpoints=settings.max_endpoints_per_user,
        )
    except UnsafeUrlError as exc:
        raise _unsafe(exc) from None
    except EndpointLimitReachedError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Endpoint limit reached") from None
    return EndpointWithSecret(**EndpointOut.model_validate(endpoint).model_dump(), secret=secret)


@router.get("", response_model=list[EndpointOut])
async def list_endpoints(
    user: AuthenticatedUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EndpointOut]:
    rows = await endpoints.list_endpoints(session, user.id, limit=limit, offset=offset)
    return [EndpointOut.model_validate(row) for row in rows]


@router.get("/{endpoint_id}", response_model=EndpointOut)
async def get_endpoint(
    endpoint_id: uuid.UUID, user: AuthenticatedUser, session: SessionDep
) -> EndpointOut:
    return EndpointOut.model_validate(await _get_or_404(session, user, endpoint_id))


@router.patch("/{endpoint_id}", response_model=EndpointOut)
async def update_endpoint(
    endpoint_id: uuid.UUID,
    body: EndpointUpdate,
    user: AuthenticatedUser,
    session: SessionDep,
    validator: ValidatorDep,
) -> EndpointOut:
    endpoint = await _get_or_404(session, user, endpoint_id)
    try:
        updated = await endpoints.update_endpoint(
            session, endpoint, body.model_dump(exclude_unset=True), validator
        )
    except UnsafeUrlError as exc:
        raise _unsafe(exc) from None
    return EndpointOut.model_validate(updated)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    endpoint_id: uuid.UUID, user: AuthenticatedUser, session: SessionDep
) -> Response:
    await endpoints.delete_endpoint(session, await _get_or_404(session, user, endpoint_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{endpoint_id}/rotate-secret", response_model=EndpointWithSecret)
async def rotate_endpoint_secret(
    endpoint_id: uuid.UUID, user: AuthenticatedUser, session: SessionDep
) -> EndpointWithSecret:
    endpoint = await _get_or_404(session, user, endpoint_id)
    secret = await endpoints.rotate_secret(session, endpoint)
    return EndpointWithSecret(**EndpointOut.model_validate(endpoint).model_dump(), secret=secret)
