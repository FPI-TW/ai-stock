from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.security import RequestUser, build_local_user
from app.db.session import check_database_connectivity

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_database_health_checker() -> Callable[[], bool]:
    return check_database_connectivity


def get_current_user(settings: SettingsDep) -> RequestUser:
    return build_local_user(settings)


CurrentUserDep = Annotated[RequestUser, Depends(get_current_user)]
