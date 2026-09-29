"""Broker-account request/response models. camelCase at the boundary; snake_case within."""

from datetime import datetime

from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, SecretStr

from app.core.config import BrokerName

# A Fubon pfx is a few KB; 64 KB leaves headroom without letting the JSON body balloon.
MAX_CERT_PFX_BYTES = 64 * 1024


class BindBrokerAccountRequest(BaseModel):
    """Login material. Every secret is `SecretStr` / `repr=False` so the model's
    repr never carries plaintext into a traceback, a log line or a locals dump."""

    model_config = ConfigDict(extra="forbid")

    broker: BrokerName
    personal_id: SecretStr = Field(alias="personalId", min_length=1, max_length=32)
    password: SecretStr = Field(min_length=1, max_length=256)
    # Decoded by pydantic; `max_length` applies to the decoded bytes.
    cert_pfx: Base64Bytes = Field(alias="certPfxBase64", min_length=1, max_length=MAX_CERT_PFX_BYTES, repr=False)
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
