from collections.abc import Callable

from app.db.session import check_database_connectivity


def get_database_health_checker() -> Callable[[], bool]:
    return check_database_connectivity
