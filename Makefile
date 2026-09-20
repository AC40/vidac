.PHONY: setup dev build test check
setup:
	uv sync
	npm ci
dev:
	npm run dev
build:
	npm run build
test:
	uv run pytest
check:
	uv run ruff check .
	uv run ruff format --check .
	npm run check
	npm run check:format
