import base64
import ipaddress
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
# Fixed dev Fernet key (32 bytes, base64url) for encrypting admin TOTP secrets in
# LOCAL_MODE only. Production must supply MFA_ENCRYPTION_KEY.
_DEV_MFA_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"dev-mfa-key-do-not-use-in-prod!!").decode()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="ai-stock-api", alias="APP_NAME")
    app_version: str = Field(default="0.5.0", alias="APP_VERSION")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    local_user_id: UUID | None = Field(default=None, alias="LOCAL_USER_ID")
    local_mode: bool = Field(default=True, alias="LOCAL_MODE")
    initial_admin_email: str | None = Field(default=None, alias="INITIAL_ADMIN_EMAIL")
    initial_admin_password: str | None = Field(default=None, alias="INITIAL_ADMIN_PASSWORD")
    initial_admin_allow_non_local: bool = Field(default=False, alias="INITIAL_ADMIN_ALLOW_NON_LOCAL")
    request_id_header: str = Field(default="X-Request-Id", alias="REQUEST_ID_HEADER")
    # 前端網址，用來把信件裡的相對連結（邀請 / 重設密碼）組成完整絕對連結。結尾不要加斜線。
    # 前端還沒部署，先用開發預設；前端上線後改 .env 的 APP_BASE_URL 即可（如 https://app.tingfong.com）。
    app_base_url: str = Field(default="http://localhost:3000", alias="APP_BASE_URL")
    # Comma-separated IPs / CIDRs of the reverse proxies (Nginx / ALB) sitting in
    # front of the app. Empty = direct connections (dev / tests): the peer IP is the
    # client. When set, the per-IP throttles resolve the real client from
    # X-Forwarded-For instead of the proxy's address (see _client_ip). Without this,
    # every request behind a proxy shares one IP bucket and users lock each other out.
    trusted_proxy_ips: str | None = Field(default=None, alias="TRUSTED_PROXY_IPS")
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

    # AWS SES email sending over the SMTP endpoint. A real sender is used only when
    # all four values are set (see resolved_ses_mailer_config); otherwise the app
    # falls back to the logging stub. The SMTP username/password are the *SES SMTP
    # credentials* (SES console → SMTP settings), not IAM access keys. In the SES
    # sandbox both sender and recipient must be verified identities.
    ses_from_address: str | None = Field(default=None, alias="SES_FROM_ADDRESS")
    ses_region: str | None = Field(default=None, alias="SES_REGION")
    ses_smtp_username: str | None = Field(default=None, alias="SES_SMTP_USERNAME")
    ses_smtp_password: str | None = Field(default=None, alias="SES_SMTP_PASSWORD")

    def _ses_values(self) -> tuple[tuple[str, str | None], ...]:
        # 欄位清單唯一出處：resolved_ses_mailer_config 與 _enforce_ses_all_or_none
        # 都吃這裡，增改 SES 欄位只動這一處，避免兩份清單默默漂移。
        return (
            ("SES_FROM_ADDRESS", self.ses_from_address),
            ("SES_REGION", self.ses_region),
            ("SES_SMTP_USERNAME", self.ses_smtp_username),
            ("SES_SMTP_PASSWORD", self.ses_smtp_password),
        )

    @property
    def resolved_ses_mailer_config(self) -> tuple[str, str, str, str] | None:
        """(from, region, smtp_username, smtp_password) when SES is fully configured,
        else None (the app then uses the logging stub)."""
        values = tuple(value for _, value in self._ses_values())
        if all(values):
            return values  # type: ignore[return-value]  # all() narrows every element to str
        return None

    @model_validator(mode="after")
    def _enforce_ses_all_or_none(self) -> "Settings":
        # Partial SES config is almost always a deploy misconfiguration: mail would
        # silently fall back to the logging stub while ops thinks it's live. Refuse to
        # boot so the gap surfaces at deploy time, not on the first send. All-empty is
        # the intentional stub and stays allowed.
        values = self._ses_values()
        if any(value for _, value in values) and not all(value for _, value in values):
            missing = [env for env, value in values if not value]
            raise ValueError(
                f"SES is partially configured; missing {', '.join(missing)}. "
                "Set all four SES_* values to send real mail, or none to use the logging stub."
            )
        return self

    # Telegram notification sync is enabled only when both values are present.
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    telegram_timeout_seconds: float = Field(default=5.0, alias="TELEGRAM_TIMEOUT_SECONDS")

    # Local V0.5 TWAP worker loop; dev endpoints remain available for manual backfill.
    twap_worker_enabled: bool = Field(default=True, alias="TWAP_WORKER_ENABLED")
    twap_worker_interval_seconds: float = Field(default=1.0, alias="TWAP_WORKER_INTERVAL_SECONDS")

    # --- L2 §15 creation caps ---
    # Per-owner active/scheduled intent caps. env defaults here; an admin-tunable
    # override is P5 (injected over the get_intent_limits dependency).
    intent_limit_per_user: int = Field(default=200, alias="INTENT_LIMIT_PER_USER")
    intent_limit_per_symbol: int = Field(default=20, alias="INTENT_LIMIT_PER_SYMBOL")

    # --- L2 §13 global mutating-endpoint rate limit ---
    # One token bucket per user, shared across every mutating endpoint (create /
    # cancel / future webhook). capacity = burst, refill = sustained tokens/sec.
    mutation_rate_limit_capacity: float = Field(default=60.0, alias="MUTATION_RATE_LIMIT_CAPACITY")
    mutation_rate_limit_refill_per_second: float = Field(default=1.0, alias="MUTATION_RATE_LIMIT_REFILL_PER_SECOND")

    # --- L2 §16 idempotency record GC ---
    # Background sweep that deletes idempotency_keys past their 24h expiry, so the
    # table (written once per create/cancel) does not grow without bound.
    idempotency_cleanup_enabled: bool = Field(default=True, alias="IDEMPOTENCY_CLEANUP_ENABLED")
    idempotency_cleanup_interval_seconds: float = Field(default=3600.0, alias="IDEMPOTENCY_CLEANUP_INTERVAL_SECONDS")

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
    # Fernet key (base64url, 32 bytes) for encrypting admin TOTP secrets at rest.
    # Required in production; LOCAL_MODE falls back to a fixed dev key.
    mfa_encryption_key: str | None = Field(default=None, alias="MFA_ENCRYPTION_KEY")

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

    @model_validator(mode="after")
    def _enforce_mfa_encryption_key(self) -> "Settings":
        if not self.local_mode and not self.mfa_encryption_key:
            raise ValueError("MFA_ENCRYPTION_KEY is required when LOCAL_MODE is false")
        return self

    @property
    def resolved_mfa_encryption_key(self) -> str:
        """The active TOTP-secret encryption key; dev fallback only in LOCAL_MODE."""
        if self.mfa_encryption_key:
            return self.mfa_encryption_key
        return _DEV_MFA_ENCRYPTION_KEY

    @property
    def resolved_jwt_access_secret(self) -> str:
        """The active access-token secret. Falls back to the dev secret only in LOCAL_MODE."""
        if self.jwt_access_secret:
            return self.jwt_access_secret
        return _DEV_JWT_ACCESS_SECRET

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """Parsed `trusted_proxy_ips`; empty list when unset (direct connections)."""
        if not self.trusted_proxy_ips:
            return []
        return [
            ipaddress.ip_network(token.strip(), strict=False)
            for token in self.trusted_proxy_ips.split(",")
            if token.strip()
        ]

    @model_validator(mode="after")
    def _validate_trusted_proxy_ips(self) -> "Settings":
        # Parse once at startup so a malformed TRUSTED_PROXY_IPS fails fast instead
        # of raising on the first authenticated request.
        _ = self.trusted_proxy_networks
        return self

    @property
    def cookie_secure(self) -> bool:
        """Refresh / CSRF cookies carry the Secure flag everywhere except LOCAL_MODE."""
        return not self.local_mode

    @property
    def cors_allow_origins_list(self) -> list[str]:
        """Parsed CORS allow-list; shared by the CORS middleware and the CSRF
        Origin/Referer check so both enforce the exact same set of origins."""
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

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
