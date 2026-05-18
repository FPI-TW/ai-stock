class SymbolError(Exception):
    pass


class UnknownSymbolError(SymbolError):
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class SymbolNotTradableError(SymbolError):
    def __init__(self, symbol: str, tradable_status: str) -> None:
        self.symbol = symbol
        self.tradable_status = tradable_status
