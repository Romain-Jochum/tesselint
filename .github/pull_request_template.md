## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checks

- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run mypy src tests`
- [ ] `uv run pytest -q` (no test edited, skipped or loosened to make it pass)
- [ ] For `web/` changes: `npm --prefix web run typecheck`, `npm --prefix web run test`, `npm --prefix web run build` (assets committed) and `npm --prefix web run e2e`
- [ ] `CHANGELOG.md` updated under `[Unreleased]` for a user-visible change
- [ ] No real backlog text, run output or API key in the diff

Live spend: US$…
