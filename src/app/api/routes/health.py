from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import SettingsDep, get_database_health_checker
from app.api.errors import ApiError, ErrorCode

router = APIRouter()

DatabaseHealthCheckerDep = Annotated[Callable[[], bool], Depends(get_database_health_checker)]


@router.get("/health")
def health(
    settings: SettingsDep,
    database_health_checker: DatabaseHealthCheckerDep,
) -> dict[str, dict[str, str]]:
    if not database_health_checker():
        raise ApiError(ErrorCode.DATABASE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)

    return {
        "data": {
            "service": settings.app_name,
            "version": settings.app_version,
            "environment": settings.app_env,
            "database": "ok",
        }
    }
