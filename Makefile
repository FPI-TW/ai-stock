.PHONY: install install-hooks dev lint format format-check typecheck commit-check pre-push-check check migrate downgrade seed test test-integration check-shioaji-isolation

DATABASE_URL ?= postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock

install:
	uv sync

install-hooks:
	-git config --unset core.hooksPath
	uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push

dev:
	uv run uvicorn app.main:app --app-dir src --reload --port 8100

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

# Fail the build if demo-provider details leak outside the quarantined
# subpackage. The only app-layer exceptions are the provider factory switch and
# settings env aliases.
check-shioaji-isolation:
	@LEAKS=$$(grep -RInE 'shioaji|Shioaji|SHIOAJI|SymbolNotAvailableInDemo|QuoteSubscriptionLimitExceeded|from app\.services\.quote\.shioaji_demo' \
			src/app --include='*.py' --exclude-dir=shioaji_demo \
			--exclude='factory.py' --exclude='config.py' || true); \
	if [ -n "$$LEAKS" ]; then \
		echo "ERROR: demo-only symbol leaked outside src/app/services/quote/shioaji_demo/:"; \
		echo "$$LEAKS"; \
		exit 1; \
	fi

migrate:
	DATABASE_URL=$(DATABASE_URL) uv run alembic upgrade head

downgrade:
	DATABASE_URL=$(DATABASE_URL) uv run alembic downgrade base

seed:
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=src uv run python -m app.db.seed

test:
	uv run pytest -s

test-integration:
	DATABASE_URL=$(DATABASE_URL) uv run pytest -m integration -s
