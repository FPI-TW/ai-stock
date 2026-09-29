"""Broker-account request/response models. camelCase at the boundary; snake_case within."""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, Base64Bytes, BaseModel, ConfigDict, Field, SecretStr
from pydantic_core import PydanticCustomError

from app.core.config import BrokerName

# A Fubon pfx is a few KB; 64 KB leaves headroom without letting the JSON body balloon.
MAX_CERT_PFX_BYTES = 64 * 1024


def _check_pfx_size(decoded: bytes) -> bytes:
    # `Field(max_length=...)` on Base64Bytes measures the *encoded* string (verified
    # on pydantic 2.12: 7 decoded bytes = 12 chars trips max_length=8), which would
    # silently cap the pfx at ~48 KB. Validate the decoded payload explicitly.
    # PydanticCustomError keeps `ctx` JSON-serialisable; a plain ValueError would
    # put the exception object in ctx and turn the 422 into a 500.
    if not decoded:
        raise PydanticCustomError("bytes_too_short", "certificate bundle is empty")
    if len(decoded) > MAX_CERT_PFX_BYTES:
        raise PydanticCustomError(
            "bytes_too_long",
            "certificate bundle exceeds {max_length} bytes",
            {"max_length": MAX_CERT_PFX_BYTES},
        )
    return decoded


CertPfx = Annotated[Base64Bytes, AfterValidator(_check_pfx_size)]


class BindBrokerAccountRequest(BaseModel):
    """Login material. Every secret is `SecretStr` / `repr=False` so the model's
    repr never carries plaintext into a traceback, a log line or a locals dump."""

    model_config = ConfigDict(extra="forbid")

    broker: BrokerName
    personal_id: SecretStr = Field(alias="personalId", min_length=1, max_length=32)
    password: SecretStr = Field(min_length=1, max_length=256)
    # Decoded by pydantic; size is checked on the decoded bytes (see CertPfx).
    cert_pfx: CertPfx = Field(alias="certPfxBase64", repr=False)
    cert_password: SecretStr = Field(alias="certPassword", min_length=1, max_length=256)


class BrokerAccountResponse(BaseModel):
    broker: str
    # Masked to the last four characters; the full number never leaves the server.
    broker_account_no: str = Field(serialization_alias="brokerAccountNo")
    status: str
    cert_expires_at: datetime = Field(serialization_alias="certExpiresAt")
    last_login_at: datetime | None = Field(serialization_alias="lastLoginAt")
    last_error: str | None = Field(serialization_alias="lastError")
    updated_at: datetime = Field(serialization_alias="updatedAt")
