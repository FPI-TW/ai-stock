from unittest.mock import MagicMock

import pytest

from app.api.symbol_errors import (
    SymbolNotTradableError,
    UnknownSymbolError,
    UnsupportedInstrumentError,
)
from app.db.models.core import Symbol
from app.services.symbol import SymbolService


@pytest.fixture
def mock_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(mock_repo: MagicMock) -> SymbolService:
    return SymbolService(mock_repo)


def test_get_tradable_symbol_success(service: SymbolService, mock_repo: MagicMock) -> None:
    mock_symbol = Symbol(
        symbol="2330",
        instrument_type="stock",
        tradable_status="tradable",
    )
    mock_repo.find_by_symbol.return_value = mock_symbol

    result = service.get_tradable_symbol("2330")

    assert result is mock_symbol
    mock_repo.find_by_symbol.assert_called_once_with("2330")


def test_get_tradable_symbol_unknown(service: SymbolService, mock_repo: MagicMock) -> None:
    mock_repo.find_by_symbol.return_value = None

    with pytest.raises(UnknownSymbolError) as exc:
        service.get_tradable_symbol("9998")

    assert exc.value.code == "UNKNOWN_SYMBOL"
    assert exc.value.status_code == 404
    assert exc.value.details == {"symbol": "9998"}


def test_get_tradable_symbol_unsupported_instrument(service: SymbolService, mock_repo: MagicMock) -> None:
    mock_symbol = Symbol(
        symbol="8888",
        instrument_type="future",
        tradable_status="tradable",
    )
    mock_repo.find_by_symbol.return_value = mock_symbol

    with pytest.raises(UnsupportedInstrumentError) as exc:
        service.get_tradable_symbol("8888")

    assert exc.value.code == "UNSUPPORTED_INSTRUMENT"
    assert exc.value.status_code == 422
    assert exc.value.details == {"instrument_type": "future"}


def test_get_tradable_symbol_not_tradable(service: SymbolService, mock_repo: MagicMock) -> None:
    mock_symbol = Symbol(
        symbol="9999",
        instrument_type="stock",
        tradable_status="halted",
    )
    mock_repo.find_by_symbol.return_value = mock_symbol

    with pytest.raises(SymbolNotTradableError) as exc:
        service.get_tradable_symbol("9999")

    assert exc.value.code == "SYMBOL_NOT_TRADABLE"
    assert exc.value.status_code == 422
    assert exc.value.details == {"symbol": "9999", "tradable_status": "halted"}


def test_find_by_symbol_unknown(service: SymbolService, mock_repo: MagicMock) -> None:
    mock_repo.find_by_symbol.return_value = None

    with pytest.raises(UnknownSymbolError):
        service.find_by_symbol("0000")


def test_symbol_string_type_preserved(service: SymbolService, mock_repo: MagicMock) -> None:
    mock_symbol = Symbol(
        symbol="0050",
        instrument_type="etf",
        tradable_status="tradable",
    )
    mock_repo.find_by_symbol.return_value = mock_symbol

    result = service.get_tradable_symbol("0050")

    assert result.symbol == "0050"
    assert isinstance(result.symbol, str)
