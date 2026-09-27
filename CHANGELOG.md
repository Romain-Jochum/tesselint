# Changelog

All notable changes to TesseLint are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-27

The first public release.

### Added

- `tesselint run` asks Jev (`typesafe/jev-1.13` on OpenRouter) how every pair of stories relates and whether each story bundles several capabilities, then writes a similarity matrix, clusters and ranked merge, overlap and split proposals to `runs/<id>/`.
- `--dry-run` and `--estimate-json` print the cost without calling anything; `--max-usd` caps the spend (US$1 by default) and `--resume` finishes an interrupted run.
- Answers are cached by the exact texts, so a re-run pays only for edited stories.
- Input as a Markdown backlog (`### E<n>` epics, `- **US-<n>.<m> Title.**` stories) or as stories v1 JSON with a JSON Schema; `import` and `export` convert between the two, and `docs/input/INPUT.md` has a prompt for converting other formats with an LLM.
- A Markdown source with no story in the expected shape exits 4 with a message naming the shape.
- `tesselint serve`, a local review UI: overview, one review case at a time with keyboard decisions, an epic and story heatmap, story, epic and cluster pages, a diff between runs, a capped re-run and an export of decisions as instructions.
- `report` to rebuild proposals with other thresholds, `synth` and `eval` for synthetic sets with planted answers and for calibration against labels, `key` to check the OpenRouter key, and `--version`.
- Measured costs, synthetic scores and calibration in `docs/results.md`.

### Security

- The UI binds to loopback only, checks the `Host` header, needs a per-launch token on every write, refuses cross-site reads under `/api` and sends a strict Content Security Policy. `serve` never reads `.env`. See `SECURITY.md`.

[Unreleased]: https://github.com/Romain-Jochum/tesselint/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Romain-Jochum/tesselint/releases/tag/v0.1.0
