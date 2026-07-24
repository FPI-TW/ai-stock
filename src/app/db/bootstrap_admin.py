"""Local bootstrap for the first login-capable admin account.

Normal account creation is invitation-based and requires an already verified
admin. A fresh local database has no login-capable admin yet, so this command
creates or updates one active admin using the same password-hashing path as the
auth API.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.passwords import build_password_hasher, hash_password, is_password_strong_enough
from app.db.models.auth import User
from app.db.session import get_session_factory
from app.domain.auth import normalize_email

DEFAULT_LOCAL_ADMIN_EMAIL = "admin@example.com"
DEFAULT_LOCAL_ADMIN_PASSWORD = "password123"


@dataclass(frozen=True, slots=True)
class BootstrapAdminConfig:
    email: str
    password: str
    allow_non_local: bool


@dataclass(frozen=True, slots=True)
class BootstrapAdminResult:
    action: str
    email: str
    user_id: str


def resolve_bootstrap_admin_config(settings: Settings) -> BootstrapAdminConfig:
    email = (settings.initial_admin_email or DEFAULT_LOCAL_ADMIN_EMAIL).strip()
    password = settings.initial_admin_password
    allow_non_local = settings.initial_admin_allow_non_local

    if password is not None:
        password = password.strip()

    if not password:
        if not settings.local_mode:
            raise RuntimeError("INITIAL_ADMIN_PASSWORD is required outside LOCAL_MODE")
        password = DEFAULT_LOCAL_ADMIN_PASSWORD

    return BootstrapAdminConfig(email=email, password=password, allow_non_local=allow_non_local)


def bootstrap_initial_admin(
    db: Session,
    settings: Settings,
    config: BootstrapAdminConfig,
    *,
    now: datetime | None = None,
    overwrite: bool = True,
) -> BootstrapAdminResult:
    """Create (or, when overwrite=True, update) an active admin.

    overwrite=True (default) keeps the env-driven bootstrap behaviour: an existing
    email is reset to an active admin. overwrite=False (the per-admin CLI) refuses
    to touch an existing account, so re-running for an already-provisioned admin
    can't silently reset their password.
    """
    if not settings.local_mode and not config.allow_non_local:
        raise RuntimeError("Initial admin bootstrap is disabled outside LOCAL_MODE")

    email = normalize_email(config.email)
    if not email:
        raise RuntimeError("INITIAL_ADMIN_EMAIL must not be blank")
    try:
        # Same rule the auth API enforces via EmailStr, so a bootstrapped admin can
        # never be a format the login/password-reset schemas would 422 on. No DNS.
        validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        raise RuntimeError(f"INITIAL_ADMIN_EMAIL is not a valid email: {email!r}") from exc
    if not is_password_strong_enough(config.password):
        raise RuntimeError("INITIAL_ADMIN_PASSWORD must be at least 8 characters")

    now_ts = now or datetime.now(UTC)
    hasher = build_password_hasher(settings)
    password_hash = hash_password(hasher, config.password)

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None and not overwrite:
        raise RuntimeError(f"admin already exists, refusing to overwrite: {email}")
    if user is None:
        user = User(
            id=uuid4(),
            email=email,
            password_hash=password_hash,
            role="admin",
            status="active",
        )
        db.add(user)
        user_id = str(user.id)
        db.commit()
        return BootstrapAdminResult(action="created", email=email, user_id=user_id)

    user.password_hash = password_hash
    user.role = "admin"
    user.status = "active"
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.disabled_at = None
    user.updated_at = now_ts
    user_id = str(user.id)
    db.commit()
    return BootstrapAdminResult(action="updated", email=email, user_id=user_id)


def main() -> None:
    settings = get_settings()
    config = resolve_bootstrap_admin_config(settings)

    session_factory = get_session_factory()
    with session_factory() as db:
        result = bootstrap_initial_admin(db, settings, config)

    print(f"Initial admin {result.action}: {result.email} ({result.user_id})")
    if settings.local_mode and config.password == DEFAULT_LOCAL_ADMIN_PASSWORD:
        print(f"Local default password: {DEFAULT_LOCAL_ADMIN_PASSWORD}")


if __name__ == "__main__":
    main()
