import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services import api_keys

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate, user: CurrentUser, session: SessionDep
) -> ApiKeyCreated:
    api_key, raw_key = await api_keys.create_api_key(session, user, body.name)
    return ApiKeyCreated(**ApiKeyOut.model_validate(api_key).model_dump(), key=raw_key)


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(user: CurrentUser, session: SessionDep) -> list[ApiKeyOut]:
    return [ApiKeyOut.model_validate(k) for k in await api_keys.list_api_keys(session, user.id)]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> Response:
    if not await api_keys.revoke_api_key(session, user.id, key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
