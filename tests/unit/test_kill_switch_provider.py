"""Unit tests for KillSwitchProvider — the in-process read cache for the global
trigger-halt flag. Covers the work-order requirement: "kill switch 旗標讀取 + cache
失效". The DB read is faked via a monkeypatched SystemFlagRepository so these stay
pure unit tests (no PostgreSQL)."""

from unittest.mock import MagicMock

import pytest

from app.services.kill_switch import GLOBAL_TRIGGER_HALT, KillSwitchProvider


def _session() -> MagicMock:
    """A session that supports `with session as db:`."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


def test_is_halted_caches_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second read inside the TTL window is served from cache — no extra DB hit."""
    reads = {"n": 0}

    class FakeRepo:
        def __init__(self, db: object) -> None: ...

        def is_enabled(self, flag_key: str) -> bool:
            assert flag_key == GLOBAL_TRIGGER_HALT
            reads["n"] += 1
            return True

    monkeypatch.setattr("app.services.kill_switch.SystemFlagRepository", FakeRepo)
    clock = {"t": 100.0}
    provider = KillSwitchProvider(lambda: _session(), ttl_seconds=30.0, monotonic=lambda: clock["t"])

    assert provider.is_halted() is True
    assert provider.is_halted() is True
    assert reads["n"] == 1  # cached, no second DB read

    clock["t"] = 131.0  # past the TTL
    assert provider.is_halted() is True
    assert reads["n"] == 2  # re-read after expiry


def test_invalidate_forces_immediate_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    """A toggle calls invalidate(); the next read must reflect the new value at once,
    not wait out the TTL."""
    reads = {"n": 0}
    values = iter([True, False])

    class FakeRepo:
        def __init__(self, db: object) -> None: ...

        def is_enabled(self, flag_key: str) -> bool:
            reads["n"] += 1
            return next(values)

    monkeypatch.setattr("app.services.kill_switch.SystemFlagRepository", FakeRepo)
    provider = KillSwitchProvider(lambda: _session(), ttl_seconds=30.0, monotonic=lambda: 0.0)

    assert provider.is_halted() is True
    provider.invalidate()
    assert provider.is_halted() is False  # re-read despite TTL not elapsing
    assert reads["n"] == 2
