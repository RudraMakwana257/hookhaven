from fastapi import APIRouter, Depends

from app.api.deps import AuthenticatedUser, SessionDep
from app.api.limits import limit_api
from app.core.time import utcnow
from app.schemas.stats import StatsOut
from app.services import stats

router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[Depends(limit_api)])


@router.get("", response_model=StatsOut)
async def get_stats(user: AuthenticatedUser, session: SessionDep) -> StatsOut:
    """Dashboard overview: last-24h activity plus the all-time dead-letter count."""
    return await stats.get_stats(session, user.id, utcnow())
