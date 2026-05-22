"""Shared pagination helpers for cursor-based list endpoints.

Repositories use the trailing row's UUID as the cursor (see
`IntentRepository.list_by_owner`). The route layer is responsible for
rejecting malformed cursor strings before they reach the repository so
the SQL layer can assume a valid UUID.
"""

from uuid import UUID

from fastapi import status

from app.api.errors import ApiError, ErrorCode


def validate_cursor(cursor: str | None) -> None:
    """Reject non-UUID cursor strings with a 400 VALIDATION_ERROR.

    A `None` cursor (first page) is allowed and short-circuits.
    """

    if cursor is None:
        return
    try:
        UUID(cursor)
    except ValueError as exc:
        raise ApiError(
            code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"cursor": "must be a valid UUID"},
        ) from exc
