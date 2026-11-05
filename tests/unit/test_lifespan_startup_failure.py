"""Lifespan must release the broker login when startup fails after `provider.startup()`.

A Fubon login is a real broker-side session; leaking it on every failed boot
eventually hits the connection cap. `provider.startup()` is the first step, so
any later failure (DB reconcile, dispatcher wiring) must still reach shutdown().
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services.quote.in_memory import InMemoryQuoteProvider


class LifecycleSpyProvider(InMemoryQuoteProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def startup(self) -> None:
        self.calls.append("startup")

    def shutdown(self) -> None:
        self.calls.append("shutdown")


def test_lifespan_shuts_provider_down_when_reconcile_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Truthy DATABASE_URL routes lifespan into the DB reconcile; the engine is
    # lazy so nothing connects. create_app() runs before the factory is broken.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    get_settings.cache_clear()
    app = create_app()
    provider = LifecycleSpyProvider()
    app.state.quote_provider = provider

    def broken_factory() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.session.get_session_factory", broken_factory)

    with pytest.raises(RuntimeError, match="db down"), TestClient(app):
        pass

    assert provider.calls == ["startup", "shutdown"]
