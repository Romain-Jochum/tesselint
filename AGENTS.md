# AGENTS.md: rules for every contributor and coding agent in this repo

TesseLint is a small Python CLI and local web UI that uses Jev (a decision model on OpenRouter) to find duplicate, overlapping and bundled user stories. The design is in `docs/DESIGN.md` (CLI and pipeline) and `docs/ui/DESIGN.md` (web UI); the input format is in `docs/input/INPUT.md`; measured results are in `docs/results.md`.

## Stack and commands

- Python 3.12, `uv`, `ruff`, `mypy --strict`, `pytest` (async tests use the anyio plugin, on asyncio). The code lives in `src/tesselint/` and the tests in `tests/`.
- Web UI: React, TypeScript and Vite in `web/`, built into `src/tesselint/server/static/` and committed, so `pip install` needs no Node.
- Setup: `uv sync` and `npm --prefix web ci`. The `slow` tests need the model2vec model once: `uv run python -c "from model2vec import StaticModel; StaticModel.from_pretrained('minishlab/potion-retrieval-32M')"`.
- Fast tests: `uv run pytest -q -m "not live and not slow"`.
- Default suite, including `slow` (`live` is excluded by `addopts`): `uv run pytest -q`.
- Live tests make real Jev calls and cost money. They are opt-in only: `uv run pytest -m live -q`.
- Checks: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src tests`.
- Web: `npm --prefix web run typecheck`, `npm --prefix web run test`, `npm --prefix web run build` (rewrites the committed assets and `build.json`; `tests/test_static_serve.py` fails when they are stale), `npm --prefix web run e2e` (Playwright on an installed Chrome).

## Tests are the spec

- Never edit, skip, xfail, loosen or delete a test, fixture, fake or helper to make it pass.
- If a test contradicts `docs/DESIGN.md` and §11 does not settle the question, leave the test failing and say so in your pull request, with the test id, the conflict and what you left.
- Tests run offline: `tests/conftest.py` sets a dummy key, an unroutable base URL and the public synthetic backlog `tests/fixtures/sample_backlog.md` as the source. Tests that need a private fixture skip when it is absent; never commit real backlog text to make them run.

## Scope

- Keep changes focused on one task, and leave unrelated files alone.
- The interfaces in DESIGN §8 are binding. Additive optional parameters are allowed; say why in the pull request.
- Keep code small, typed and readable, with one mechanism per job. Add no new dependency without saying why.

## Secrets

- The OpenRouter key lives only in the gitignored `.env` (see `.env.example`). Code loads it with python-dotenv; `tesselint serve` never reads `.env`, only the CLI subprocesses it starts do.
- Never print, log, echo, `cat`, grep or commit it, and never run `env` or `printenv`. Keep it out of exceptions, reprs and test output.

## Spend

- Run `tesselint run` with `--dry-run` first when in doubt: it prints the estimate and makes no call. `--max-usd` caps a run.
- Tests never call Jev except `-m live` (under US$0.001).
- Report every live spend in your pull request.

## Private material

- `private/` is gitignored and holds material that must not be published (a real backlog snapshot, raw probe answers, internal notes). Never move anything from it into the tracked tree, and never quote a real backlog in docs, tests or commit messages.
- The source backlog is read-only: TesseLint proposes, it never edits the source.

## Git

- Use conventional commits (`feat(scope): …`, `fix`, `test`, `docs`, `refactor`, `build`, `chore`).
- `git add` named paths only, never `-A` or `.`.
- `.env`, `runs/`, `.cache/`, `.tesselint/` and `private/` stay ignored.
