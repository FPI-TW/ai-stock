from functools import lru_cache
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="ai-stock-api", alias="APP_NAME")
    app_version: str = Field(default="0.5.0", alias="APP_VERSION")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    local_user_id: UUID | None = Field(default=None, alias="LOCAL_USER_ID")
    local_mode: bool = Field(default=True, alias="LOCAL_MODE")
    request_id_header: str = Field(default="X-Request-Id", alias="REQUEST_ID_HEADER")

    @model_validator(mode="after")
    def _enforce_local_user_id(self) -> "Settings":
        if self.local_mode and self.local_user_id is None:
            raise ValueError("LOCAL_USER_ID is required when LOCAL_MODE is true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
