# Contributing to TesseLint

Thanks for your interest. Bug reports, fixes and small focused improvements are welcome. For a larger change, open an issue first so we can agree on the approach. `AGENTS.md` holds the full rules, for people and coding agents alike; this page is the short version.

## Setup

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and, for the web UI, Node 22 or later (CI uses 24).

```sh
uv sync                  # Python dependencies and dev tools
npm --prefix web ci      # web UI dependencies (only if you touch web/)
cp .env.example .env     # only for live runs; tests never need a key
```

The `slow` tests use a small local embedding model. Download it once:

```sh
uv run python -c "from model2vec import StaticModel; StaticModel.from_pretrained('minishlab/potion-retrieval-32M')"
```

## Checks

Run these before you open a pull request; CI runs the same.

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -q                        # everything except `live`
uv run pytest -q -m "not live and not slow"   # the fast subset
```

For changes under `web/`:

```sh
npm --prefix web run typecheck
npm --prefix web run test
npm --prefix web run build   # rewrites src/tesselint/server/static/; commit the result
npm --prefix web run e2e     # Playwright, needs Google Chrome installed
```

The built UI is committed so that `pip install` needs no Node. `tests/test_static_serve.py` fails when the committed assets are older than `web/src/`, so rebuild and commit them with your change.

## Conventions

- **Tests are the spec.** Never edit, skip, loosen or delete a test to make it pass. If a test and `docs/DESIGN.md` disagree, say so in the pull request.
- **No real money in tests.** Tests run offline against fakes. `live` tests make real Jev calls, cost money and are opt-in only (`uv run pytest -m live`). Report any live spend in your pull request.
- **No private data.** Test fixtures are synthetic (`tests/fixtures/sample_backlog.md`, `tests/fixtures/ui/`). Never commit a real backlog, raw model answers that quote one, or an API key. `private/` is gitignored for local material.
- **Small and typed.** `mypy --strict` passes; one mechanism per job; no new dependency without a reason in the pull request.
- **Commits.** Conventional commits (`feat(scope): …`, `fix`, `test`, `docs`, `refactor`, `build`, `chore`), and `git add` named paths rather than `-A`.
- **Pull requests.** The template lists the checks above and asks for your live spend (`US$0` if you ran no `live` test). Add a line under `[Unreleased]` in `CHANGELOG.md` for a user-visible change.

## Where things are

- `src/tesselint/`: the CLI, pipeline and Jev client; `src/tesselint/server/`: the local web server.
- `web/`: the React UI; `web/e2e/`: Playwright tests.
- `docs/DESIGN.md`, `docs/ui/DESIGN.md`, `docs/input/INPUT.md`: design and input format. `docs/results.md`: measured results.
- `CHANGELOG.md`: what changed in each release.

## License

By contributing, you agree that your contributions are licensed under the MIT License (see `LICENSE`).
