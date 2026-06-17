"""Per-admin CLI for provisioning a login-capable admin account.

Unlike the env-driven `bootstrap_admin` (one admin from INITIAL_ADMIN_*), this
takes the email as an argument so it can be re-run to add more admins without
editing env. It reuses `bootstrap_initial_admin` for the actual create, with
overwrite=False so re-running for an existing email refuses rather than silently
resetting that admin's password.

The created admin starts with mfa_enabled=false; admin endpoints stay gated until
the admin enrols 2FA themselves (POST /admin/2fa/setup -> /verify) after first login.

Usage (in prod, against the compose DB on the host):
    docker compose -f docker-compose.prod.yml --env-file .env.prod \
        run --rm app python -m app.db.create_admin --email dev@example.com

The password is auto-generated and printed once by default. To set a specific
password, pass it via the CREATE_ADMIN_PASSWORD environment variable — never a CLI
argument, which would leak into `ps`, shell history, and docker run events.
"""

import argparse
import os
import secrets

from app.core.config import get_settings
from app.db.bootstrap_admin import BootstrapAdminConfig, bootstrap_initial_admin
from app.db.session import get_session_factory

# token_urlsafe(16) -> ~22 chars, comfortably clears the >= 8 strength check.
_GENERATED_PASSWORD_BYTES = 16
# Optional custom-password source. Read from the environment (not a CLI arg) so a
# chosen password never lands in `ps` / shell history / docker run events.
_PASSWORD_ENV = "CREATE_ADMIN_PASSWORD"


def main() -> None:
    parser = argparse.ArgumentParser(description="建立一個 admin 帳號（可重複執行，每次一個）")
    parser.add_argument("--email", required=True, help="新 admin 的 email")
    args = parser.parse_args()

    custom_password = os.environ.get(_PASSWORD_ENV) or None
    generated = custom_password is None
    password = custom_password or secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)

    settings = get_settings()
    # This is the explicit "create a real admin" tool, so it is allowed to run in
    # production (LOCAL_MODE=false) — that is the whole point.
    config = BootstrapAdminConfig(email=args.email, password=password, allow_non_local=True)

    session_factory = get_session_factory()
    with session_factory() as db:
        result = bootstrap_initial_admin(db, settings, config, overwrite=False)

    print(f"admin {result.action}: {result.email} ({result.user_id})")
    if generated:
        print(f"一次性密碼（請安全地交給該 admin，勿存檔）：{password}")
    print("⚠️ 該 admin 首次登入後請立即註冊 2FA（POST /admin/2fa/setup → /verify）才能使用 admin 端點。")


if __name__ == "__main__":
    main()
