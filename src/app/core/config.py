from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

QuoteProviderName = Literal["shioaji_demo", "in_memory"]
REQUIRED_CURRENT_PRICE_PROVIDER: QuoteProviderName = "shioaji_demo"
CURRENT_PRICE_SOURCE_NAME = "shioaji"

# Fixed dev secret used ONLY when LOCAL_MODE=true and no JWT_ACCESS_SECRET is set,
# so local runs / tests exercise the full auth flow without env wiring. Production
# (LOCAL_MODE=false) fails fast when the env secret is missing — never this value.
_DEV_JWT_ACCESS_SECRET = "dev-only-insecure-jwt-access-secret-do-not-use-in-prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="ai-stock-api", alias="APP_NAME")
    app_version: str = Field(default="0.5.0", alias="APP_VERSION")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    local_user_id: UUID | None = Field(default=None, alias="LOCAL_USER_ID")
    local_mode: bool = Field(default=True, alias="LOCAL_MODE")
    request_id_header: str = Field(default="X-Request-Id", alias="REQUEST_ID_HEADER")
    cors_allow_origins: str = Field(
        default=(
            "http://localhost:3000,http://127.0.0.1:3000,"
            "http://localhost:3001,http://127.0.0.1:3001,"
            "http://localhost:3100,http://127.0.0.1:3100"
        ),
        alias="CORS_ALLOW_ORIGINS",
    )

    # quote provider switch — single mechanism for choosing the runtime quote source.
    # V0.5 accepts: "shioaji_demo" (default, runtime) | "in_memory" (tests / CI).
    quote_provider: QuoteProviderName = Field(default="shioaji_demo", alias="QUOTE_PROVIDER")

    # Shioaji-demo-only settings; read only when quote_provider == "shioaji_demo".
    shioaji_api_key: str | None = Field(default=None, alias="SHIOAJI_API_KEY")
    shioaji_secret_key: str | None = Field(default=None, alias="SHIOAJI_SECRET_KEY")
    shioaji_max_subscriptions: int = Field(default=5, alias="SHIOAJI_MAX_SUBSCRIPTIONS")
    shioaji_simulation: bool = Field(default=False, alias="SHIOAJI_SIMULATION")
    shioaji_demo_allowed_symbols: str | None = Field(default=None, alias="SHIOAJI_DEMO_ALLOWED_SYMBOLS")

    # Telegram notification sync is enabled only when both values are present.
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    telegram_timeout_seconds: float = Field(default=5.0, alias="TELEGRAM_TIMEOUT_SECONDS")

    # Local V0.5 TWAP worker loop; dev endpoints remain available for manual backfill.
    twap_worker_enabled: bool = Field(default=True, alias="TWAP_WORKER_ENABLED")
    twap_worker_interval_seconds: float = Field(default=1.0, alias="TWAP_WORKER_INTERVAL_SECONDS")

    # --- L1 auth / session ---
    # HS256 signing secret for short-lived access JWTs. Required in production
    # (LOCAL_MODE=false); LOCAL_MODE falls back to a fixed dev secret. Never log it.
    jwt_access_secret: str | None = Field(default=None, alias="JWT_ACCESS_SECRET")
    access_token_ttl_user_seconds: int = Field(default=900, alias="ACCESS_TOKEN_TTL_USER_SECONDS")  # 15 min
    access_token_ttl_admin_seconds: int = Field(default=300, alias="ACCESS_TOKEN_TTL_ADMIN_SECONDS")  # 5 min
    refresh_token_ttl_user_seconds: int = Field(default=2_592_000, alias="REFRESH_TOKEN_TTL_USER_SECONDS")  # 30 d
    refresh_token_ttl_admin_seconds: int = Field(default=43_200, alias="REFRESH_TOKEN_TTL_ADMIN_SECONDS")  # 12 h
    # argon2id cost parameters (env override per工單 工程注意事項).
    argon2_time_cost: int = Field(default=3, alias="ARGON2_TIME_COST")
    argon2_memory_cost: int = Field(default=65_536, alias="ARGON2_MEMORY_COST")
    argon2_parallelism: int = Field(default=4, alias="ARGON2_PARALLELISM")

    @model_validator(mode="after")
    def _enforce_local_user_id(self) -> "Settings":
        if self.local_mode and self.local_user_id is None:
            raise ValueError("LOCAL_USER_ID is required when LOCAL_MODE is true")
        return self

    @model_validator(mode="after")
    def _enforce_jwt_access_secret(self) -> "Settings":
        # Production must supply its own secret; refuse to boot on the dev fallback.
        if not self.local_mode and not self.jwt_access_secret:
            raise ValueError("JWT_ACCESS_SECRET is required when LOCAL_MODE is false")
        return self

    @property
    def resolved_jwt_access_secret(self) -> str:
        """The active access-token secret. Falls back to the dev secret only in LOCAL_MODE."""
        if self.jwt_access_secret:
            return self.jwt_access_secret
        return _DEV_JWT_ACCESS_SECRET

    @property
    def cookie_secure(self) -> bool:
        """Refresh / CSRF cookies carry the Secure flag everywhere except LOCAL_MODE."""
        return not self.local_mode

    @model_validator(mode="after")
    def _enforce_shioaji_credentials(self) -> "Settings":
        if self.quote_provider == "shioaji_demo":
            missing = [
                env
                for env, value in (
                    ("SHIOAJI_API_KEY", self.shioaji_api_key),
                    ("SHIOAJI_SECRET_KEY", self.shioaji_secret_key),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"QUOTE_PROVIDER=shioaji_demo requires {', '.join(missing)}; "
                    "set them in .env or switch to QUOTE_PROVIDER=in_memory for tests."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
