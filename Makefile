.PHONY: install install-hooks dev lint format format-check typecheck commit-check pre-push-check check migrate downgrade test test-integration check-shioaji-isolation

DATABASE_URL ?= postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock

install:
	uv sync

install-hooks:
	-git config --unset core.hooksPath
	uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push

dev:
	uv run uvicorn app.main:app --app-dir src --reload

lint:
	uv run ruff check .

format:
	uv run ruff check --fix .
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy src tests

commit-check: format lint format-check typecheck

pre-push-check: test

check: lint format-check typecheck check-shioaji-isolation test

# Fail the build if a demo-only quote symbol leaks outside its quarantined
# subpackage. BE-V0.5-13 requires every Shioaji-specific concern to live in
# `app/services/quote/shioaji_demo/` so V1 migration can `rm -rf` it in one
# shot. We grep for the truly load-bearing identifiers — the SDK import, the
# concrete provider / error class names — rather than the bare word "shioaji",
# which legitimately appears in env aliases, factory dispatch, error messages
# and docstrings that explain the isolation rule.
check-shioaji-isolation:
	@LEAKS=$$(grep -RInE 'ShioajiQuoteProvider|ShioajiClient|SymbolNotAvailableInDemo|QuoteSubscriptionLimitExceeded|^import shioaji|^from shioaji|from app\.services\.quote\.shioaji_demo' \
		src/app --include='*.py' --exclude-dir=shioaji_demo \
		--exclude='factory.py' || true); \
	if [ -n "$$LEAKS" ]; then \
		echo "ERROR: demo-only symbol leaked outside src/app/services/quote/shioaji_demo/:"; \
		echo "$$LEAKS"; \
		exit 1; \
	fi

migrate:
	DATABASE_URL=$(DATABASE_URL) uv run alembic upgrade head

downgrade:
	DATABASE_URL=$(DATABASE_URL) uv run alembic downgrade base

test:
	uv run pytest

test-integration:
	DATABASE_URL=$(DATABASE_URL) uv run pytest -m integration
