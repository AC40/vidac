# Vidac workspace

Vidac prepares footage into a reusable virtual-clip library and revisioned rough cuts. Read `README.md` first. Domain/storage/timing contracts are in `docs/architecture.md`; editor limitations are in `docs/handoff.md`.

## Developing the app

- React/TypeScript lives in `apps/web`; Python lives in `packages/core/src/vidac`.
- Use `uv run` for Python commands. Run from the repo root.
- Keep media processing, edit validation and export rules in the Python engine; the CLI and API share it.
- Preserve all user changes and workspace data. Never overwrite/delete camera originals or a saved edit revision.
- Source frame ranges use rational fps, inclusive in and exclusive out. Models may propose data, never executable render commands.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `npm run check`, `npm run check:format`, and `npm run build` before handing off code.
- Browser verification: `npm run test:browser`; see `docs/development.md` for Chromium setup. Use synthetic/test media, not unrelated user files.
- Keep live Resolve/FCP/Ollama claims separate from mocked contract tests. No model download, cloud upload or publishing is implicit in ordinary development work.

## Using the app as an editing agent

Follow `docs/agent-workflow.md`. Inspect the real catalogue via `vidac search`/`assets`, get the plan schema with `vidac schema`, save through `vidac import-plan`, then render for review. Don't edit SQLite directly to perform editorial work. Never claim unclassified footage has been visually understood. Ask before downloading large models or moving, deleting or uploading originals.
