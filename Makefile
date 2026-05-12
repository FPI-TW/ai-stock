.PHONY: install dev lint format format-check test test-integration

install:
	uv sync

dev:
	uv run uvicorn app.main:app --app-dir src --reload

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

test:
	uv run pytest

test-integration:
	DATABASE_URL=postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock uv run pytest -m integration
