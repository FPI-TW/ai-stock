"""Unit tests for IdempotencyManager + hash_payload (§16).

Covers the work-order requirement: "idempotency hash 判定（same/diff payload）" plus
the manager's replay / conflict / expiry behaviour. A small in-memory fake repo and a
MagicMock session keep these pure unit tests.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.repositories.idempotency_repository import IdempotencyRecord, IdempotencyRepository
from app.services.idempotency import IdempotencyConflictError, IdempotencyManager, hash_payload

USER = uuid4()
NOW = datetime(2026, 6, 12, tzinfo=UTC)


class FakeRepo:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, str], IdempotencyRecord] = {}

    def get(self, user_id: UUID, key: str) -> IdempotencyRecord | None:
        return self.records.get((user_id, key))

    def create(
        self,
        *,
        user_id: UUID,
        key: str,
        endpoint: str,
        request_hash: str,
        response_snapshot: dict[str, Any],
        now: datetime,
        expires_at: datetime,
    ) -> None:
        self.records[(user_id, key)] = IdempotencyRecord(
            request_hash=request_hash, response_snapshot=response_snapshot, expires_at=expires_at
        )

    def delete(self, user_id: UUID, key: str) -> None:
        self.records.pop((user_id, key), None)


def _manager() -> tuple[IdempotencyManager, FakeRepo]:
    repo = FakeRepo()
    return IdempotencyManager(MagicMock(), cast(IdempotencyRepository, repo)), repo


def test_hash_is_key_order_independent_and_payload_sensitive() -> None:
    assert hash_payload({"a": 1, "b": 2}) == hash_payload({"b": 2, "a": 1})
    assert hash_payload({"a": 1}) != hash_payload({"a": 2})


def test_replay_returns_stored_snapshot_without_re_executing() -> None:
    manager, _ = _manager()
    runs = {"n": 0}

    def execute() -> dict[str, int]:
        runs["n"] += 1
        return {"result": runs["n"]}

    first = manager.run(user_id=USER, key="k", endpoint="create", payload={"a": 1}, now=NOW, execute=execute)
    second = manager.run(user_id=USER, key="k", endpoint="create", payload={"a": 1}, now=NOW, execute=execute)

    assert first == {"result": 1}
    assert second == {"result": 1}  # replayed, not re-run
    assert runs["n"] == 1


def test_same_key_different_payload_conflicts() -> None:
    manager, _ = _manager()
    manager.run(user_id=USER, key="k", endpoint="create", payload={"a": 1}, now=NOW, execute=lambda: {"r": 1})

    with pytest.raises(IdempotencyConflictError):
        manager.run(user_id=USER, key="k", endpoint="create", payload={"a": 2}, now=NOW, execute=lambda: {"r": 2})


def test_expired_record_is_reusable() -> None:
    manager, _ = _manager()
    manager.run(user_id=USER, key="k", endpoint="create", payload={"a": 1}, now=NOW, execute=lambda: {"r": 1})

    later = NOW + timedelta(hours=25)  # past the 24h TTL
    runs = {"n": 0}

    def execute() -> dict[str, int]:
        runs["n"] += 1
        return {"r": 99}

    result = manager.run(user_id=USER, key="k", endpoint="create", payload={"a": 2}, now=later, execute=execute)
    assert result == {"r": 99}  # old record expired → re-executed despite same key
    assert runs["n"] == 1
