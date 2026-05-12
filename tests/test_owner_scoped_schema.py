import pytest
from pydantic import ValidationError

from app.api.schemas.base import OwnerScopedRequestModel


class _ExampleRequest(OwnerScopedRequestModel):
    symbol: str


def test_declared_field_accepted() -> None:
    request = _ExampleRequest.model_validate({"symbol": "2330"})

    assert request.symbol == "2330"


def test_extra_owner_user_id_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _ExampleRequest.model_validate({"symbol": "2330", "owner_user_id": "00000000-0000-0000-0000-000000000002"})

    errors = exc_info.value.errors()
    assert any(error["type"] == "extra_forbidden" and error["loc"] == ("owner_user_id",) for error in errors)


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _ExampleRequest.model_validate({"symbol": "2330", "unexpected": True})

    errors = exc_info.value.errors()
    assert any(error["type"] == "extra_forbidden" and error["loc"] == ("unexpected",) for error in errors)
