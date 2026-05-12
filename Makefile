.PHONY: install install-hooks dev lint format format-check typecheck commit-check pre-push-check check migrate downgrade test test-integration

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

check: lint format-check typecheck test

migrate:
	DATABASE_URL=$(DATABASE_URL) uv run alembic upgrade head

downgrade:
	DATABASE_URL=$(DATABASE_URL) uv run alembic downgrade base

test:
	uv run pytest

test-integration:
	DATABASE_URL=$(DATABASE_URL) uv run pytest -m integration
