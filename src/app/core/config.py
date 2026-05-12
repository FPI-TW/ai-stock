from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="ai-stock-api", alias="APP_NAME")
    app_version: str = Field(default="0.5.0", alias="APP_VERSION")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    local_user_id: str = Field(default="local-user", alias="LOCAL_USER_ID")
    local_mode: bool = Field(default=True, alias="LOCAL_MODE")
    request_id_header: str = Field(default="X-Request-Id", alias="REQUEST_ID_HEADER")


@lru_cache
def get_settings() -> Settings:
    return Settings()
