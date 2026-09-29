"""Pure parts of the bind command: pfx parsing and the runtime import boundary."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.pfx_helpers import build_test_pfx

from app.commands.broker_account import _cert_expires_at
from app.domain.broker_account import BrokerLoginFailedError


def test_cert_expiry_is_read_from_the_pfx() -> None:
    expires = datetime(2027, 6, 30, 12, 0, tzinfo=UTC)
    pfx = build_test_pfx(password="secret", not_valid_after=expires)

    assert _cert_expires_at(pfx, "secret") == expires


def test_wrong_cert_password_is_a_safe_login_failure() -> None:
    pfx = build_test_pfx(password="secret")

    with pytest.raises(BrokerLoginFailedError) as info:
        _cert_expires_at(pfx, "wrong")
    assert info.value.code == "cert_invalid"


def test_garbage_pfx_is_a_safe_login_failure() -> None:
    with pytest.raises(BrokerLoginFailedError) as info:
        _cert_expires_at(b"not a pfx", "x")
    assert info.value.code == "cert_invalid"


def test_in_memory_runtime_never_imports_the_fubon_package() -> None:
    """Fresh interpreter: other tests in this process import the Fubon modules on
    purpose, so `sys.modules` here would prove nothing."""
    script = "import sys, app.main; print(sorted(n for n in sys.modules if n.startswith('app.services.quote.fubon')))"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "QUOTE_PROVIDER": "in_memory",
        "LOCAL_MODE": "true",
        "LOCAL_USER_ID": "00000000-0000-0000-0000-000000000001",
    }
    env.pop("DATABASE_URL", None)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True)

    assert result.stdout.strip() == "[]"
