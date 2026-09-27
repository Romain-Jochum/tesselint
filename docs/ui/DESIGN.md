# TesseLint web UI · design (binding)

Contract for test writers and implementers, built on ten reviewer jobs (J1–J10) and `docs/input/INPUT.md`. Names, paths, fields and keys are binding; log any additive optional parameter.

## 1. Stack (ADR-UI-1, accepted)

Scores 1–5, higher is better; Days is the build estimate.

| Candidate | PM use | Days | Reliability | Footprint | Tests | Reference feel |
|---|---|---|---|---|---|---|
| **A. Vite + React 19 + TS + Tailwind 4 SPA, built assets committed, served by `tesselint serve` (Starlette 1.7 + uvicorn 0.54)** | 5 | 3–4 | 4 | 4 | 5 | 5 |
| B. Next.js 16 like the reference, calling the CLI | 5 | 4–5 | 3 | 1 | 4 | 5 |
| C. Starlette + Jinja2 + htmx | 3 | 3 | 4 | 5 | 3 | 2 |
| D. Static `report --html` | 2 | 1.5 | 5 | 5 | 3 | 3 |
| E. D + stdlib `http.server` + vanilla JS | 4 | 3 | 3 | 5 | 3 | 3 |
| F. Preact + htm ESM, no build | 4 | 3.5 | 3 | 5 | 2 | 3 |

**Choice: A.** Triage keys, optimistic saves, the heatmap and run progress are client work that needs components, types and component tests; A keeps one Python-owned schema behind a JSON API and the reference's React, Tailwind and tokens, and committed assets mean users need no Node.

Rejected: B puts Node (185 MB) in the runtime and duplicates the schemas; C still needs client JS; D cannot keep decisions; E hand-rolls routing on an unhardened server; F has no type checks.

**Boundary (fixed).** Only a `tesselint` CLI subprocess reads `.env` or reaches OpenRouter. `serve` never loads `.env`, never imports `tesselint.jev`, `httpx` or `dotenv`, gets key presence and balance from `tesselint key --json`, and never writes sources or run directories.

## 2. Information architecture

**Chrome.** Header: run switcher (ref, `real`/`synthetic` badge, short hash), `key present · US$1.12 left`, an amber `source edited: 3` chip linking to Re-run, `saved · rev 41`, theme toggle. Tabs: Overview, Review (open count), Matrix, Clusters, Runs, Export. Every story ID links to its detail, title on hover. Keys: `/` search, `x` raw JSON, `?` help, `g` + `o/r/m/c/u/e` tabs, `t` theme.

**Trust banner** (Overview and Review; collapsible, never removed):

> Merges and overlaps are a ranked review queue, not decisions: Jev's relation answers failed calibration (κ 0.11, 160 labelled pairs). o ranks well: 0 of 83 labelled pairs below 0.5 were related; 9 of 10 related pairs had o ≥ 0.7. Splits passed (precision 0.67, AUC 0.82) but are all borderline.

Figures come from the run's `calibration.json`, else the newest same-kind run's (cited), else "not calibrated". Every merge and overlap card carries a chip from `O_BANDS` (the calibration figures of `docs/results.md`, a constant in `web/src/lib/trust.ts`), e.g. `o 0.7–0.9: 6/36 related`. Nothing is hidden by default; `preset=o70` is one click away.

**Routes.** `/r/:run/{overview,review,matrix,clusters,export}`, `/r/:run/story/:id`, `/r/:run/epic/:id`, `/runs`, `/runs/diff`, `/runs/new`, `/runs/job/:id`; `/` opens the latest run.

### Overview (J8, J6, J5, J10)

**Tiles:** stories, `50 groups · 101 merges`, overlaps, splits, `38/142 decided · 4 stale`, cost.

**What changed:** vs the previous comparable run (stories +a −r ~e, proposals new or gone, decisions to re-review) and vs the current source (unparsed lines, "a re-run would exit 4", and "Re-run (estimate first)").

**Epic health:** a sortable table of live stories, stories in proposals, groups/overlaps/splits, within-epic pairs with o ≥ t, top partner epic, share decided. **Hubs:** stories kept by ≥ 3 merges. A print stylesheet; no runs shows the `--dry-run` command; a partial run shows "N of M pairs answered · Resume".

### Review (J1, J2)

**Layout.** A 320 px case list, the case pane and a sticky decision bar; at 1440 × 900 both texts, evidence and conflicts fit without page scroll.

**Cases** (§4.3): one merge group per dropped story ("Where does US-4.4 go?") with its containers as options, strongest first; one case per split and per overlap. Rows show ID, title, strength, markers and a status edge with an icon.

**Merge pane:** options `1`–`9` (relation, m, o, top-2 fwd/swap bars, cluster, cos); the dropped story beside the chosen container with bundled scores and shared phrases highlighted; "also related"; a conflict strip ("US-3.3 is split S4 · undecided · `c`").

**Split pane:** bullets, bundled score, reached clusters, stories it would absorb, conflicts, a "how to split" note hint. **Overlap pane:** members in columns and an edge table.

**Keys:** `a` accept (the group's other options derive `superseded`, not stored), `r`/`R` reject one/all, `d` defer, `n` note (Ctrl+Enter saves; a note alone keeps `action: null`), `u` undo the last batch, `j`/`k` move, `1`–`9` pick, `o`/`O` open a story, `c` jump to the conflict. The next case opens after 150 ms and `aria-live` says "Accepted merge US-4.4 into US-3.3 · u to undo"; keys are inert in inputs except Esc and Ctrl+Enter. Stale cases show a word diff of stored against current text.

**URL:** `case`, `opt`, `kind`, `epic`, `min`, `bl=all|only|hide`, `status=open|stale|decided|all|orphaned` (default `open` = undecided + stale), `preset=o70`, `sort=strength|doc`, `th=m0.5,c0.7,s0.8` (re-derives in memory, never writes). A failed save rolls back and offers Retry.

### Matrix (J7, J5)

**Controls:** level (Epics, the default, or Stories), order (document/cluster), `t` (default: the run's `cluster`), epic metric (count/share/mean o), Table.

**Epic level:** 23 × 23 cells; tooltip "E3 × E4 · 3 of 30 pairs ≥ 0.70 · mean o 0.21 · top US-3.3 ⊃ US-4.4 0.97"; a click drills into Ei × Ej. **Story level:** n × n with epic boundaries, masked diagonal and cells below t faded; hover outlines the cell and shows IDs, titles, o, m, direction and proposal; brush zooms, Esc resets; arrow keys move a focus cell (`aria-live`); click or Enter opens the pair drawer (both texts, fwd/swap probabilities, decision buttons). **Table** is the colour-free twin: pairs ≥ t. **URL:** `level`, `order`, `t`, `rows`, `cols`, `pair=US-1.1~US-20.2`, `view`.

### Other views

**Story (J4):** fields, status against the source, neighbours ranked by o (top 20, o ≥ 0.4) with relation in words, proposal and decision; its cluster, cases and decision history. **Epic (J5):** its own heatmap, partner epics as ranked bars linking to matrix blocks, its proposals. **Clusters (J7):** by mean o; `?c=C5` shows members side by side.

**Runs (J6):** ref, kind, source, scope, status, cost, staleness; dry runs only with `include=dry`. Ticking two opens `/runs/diff?a=&b=`: stories added, removed, edited (word diff) and renamed; proposals new, gone, changed; decisions to re-review.

**Re-run (J9).** `/runs/new?source=0&tier=t3` or `?resume=<ref>`.

1. Pick an allowlisted source and scope.
2. A free **estimate** shows to-ask, cached, US$, ETA, balance and the exact command.
3. **Confirm:** the cap defaults to `min(ceil_cent(1.3 × estimate), remaining, ui_max_usd)`, beside the tick box "I accept spending up to US$X". Start stays disabled unless estimate ≤ cap ≤ min(remaining, `ui_max_usd`).
4. The **job** page shows phase bars, spend vs cap, the log tail and Cancel.

Exit-code messages: 3 "Stopped at the cap; resume pays only missing answers"; 4 the CLI's error line; 5 "Jev refused the key or credits"; 6 "Too many failed calls"; 130 "Cancelled; partial outputs kept"; 1 "Unexpected; run it with --debug".

**Export (J3):** accepted merges (by kept story), then splits, then overlaps; stale items only with `include_stale=1`; warnings for a merge plus a split on one story and for chains (X→Y, Y→Z suggests X→Z). Download .md or .json, copy, or write to `.tesselint/exports/`.

## 3. Decisions store

**Location:** `<state>/decisions.json`; `<state>` is `.tesselint/` (or `--state`, `TESSELINT_STATE_DIR`), gitignored (backlog text), also holding `.bak`, `exports/`, `jobs/`, `.lock`; never inside a run.

**Keys** (`proposal_key`): `merge:<keep>><drop>`, `split:<id>`, `overlap:` + sorted member IDs joined by `+`. IDs cannot contain `>`, `+` or `:`; run-local IDs such as M1 never appear.

**Record:** kind, target, stories, action (`accept|reject|defer|null`), note; per story text hash, text and title; decisive value, run ref, snapshot hash, time. Hashes are `text_hash(render(story, "full"))`.

**Computed per run, never stored:** `status` is `current` (all hashes equal), `stale` (a hash differs; `changed` names it; the case reopens) or `missing` (a story is gone); `proposed` false means orphaned (still shown and exported, never deleted); `superseded_by`; `applied` (merges only: the source already holds `drop — merged into keep`).

**Renumbering:** a decision follows its ID; if a missing ID's hash reappears under a new ID, the UI offers "carry over?" (one confirm). A changed overlap membership orphans the old decision with a "similar" hint (Jaccard ≥ 0.5).

**History:** each POST is one batch (`rev += 1`, append `{rev, at, changes:[{key, before, after}]}`); undo appends the inverse batch with `undo_of`.

**Writes** (one `threading.Lock`): re-read if mtime or size changed (hand edits win), copy to `.bak`, write `.tmp`, fsync, `os.replace`, fsync the directory. An unreadable file returns 500 `decisions_unreadable` and blocks writes; it is never overwritten. **Locking:** `.lock` (`O_EXCL`, holds the pid) makes a second `serve` exit 2 unless that pid is dead; tabs refetch when `rev` jumps by more than 1.

## 4. Backend additions (Python)

### 4.1 Serve

```
tesselint serve [--host 127.0.0.1] [--port 8765] [--runs runs] [--state .tesselint] [--source PATH]… [--ui-max-usd 1.0] [--no-open]
```

One uvicorn process; a non-loopback host or busy port exits 2; `main()` skips `load_dotenv` when the first positional argument is `serve`.

**`LocalGuard` (ASGI):** `Host` must be `127.0.0.1:P`, `localhost:P` or `[::1]:P` (else 403 `bad_host`). A non-GET needs an `X-TesseLint-Token` equal to a startup `secrets.token_urlsafe(32)` injected as `<meta name="tesselint-token" content="__TESSELINT_TOKEN__">`, any `Origin` equal to `http://<Host>`, any `Sec-Fetch-Site` of same-origin or none, and a JSON body (else 403 `csrf` or 415). A GET or HEAD under `/api` needs any `Sec-Fetch-Site` of same-origin or none too (else 403 `csrf`). Responses carry the CSP `default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`, plus `nosniff` and `no-referrer`. `/api` responses are also `no-store`.

Refs resolve only through the index; sources only through an allowlist (`--source`, `TESSELINT_SOURCE`, runs' `source.path`) by `source_id`; the server names export files. Logs and errors redact `sk-or-[A-Za-z0-9_-]+`; a 500 has no details.

### 4.2 Run index

A **ref** is the run directory under the runs root with `/` → `~`, matching `^[A-Za-z0-9_-][A-Za-z0-9._-]*(~[A-Za-z0-9_-][A-Za-z0-9._-]*){0,3}$`; refs are the identity because run IDs collide (`t1/` and `dry/` both hold `20260926-141229`). Scan: `os.walk(followlinks=False)`, depth 4.

**New `run.json` fields:** `source {path, format}`; `kind` (`synthetic` if the input sets `"x-synthetic": true`, its converter starts with `tesselint synth`, or a `truth.json` sits beside it; else `real`); `dry_run`; `input {format, version, converter, render}` for JSON. Legacy runs infer `kind` from a `synth` ref segment (`kind_inferred`) and are dry when `calls.live + calls.cached == 0` and `jobs.fwd > 0`. **Latest:** newest non-dry `complete` real run. **Previous:** newest earlier run of the same kind and tier.

### 4.3 Derive, cases, diff

`derive(run_dir, th)` is `report` without writes (0.23 s cold on the final run); `report` becomes derive plus write. The server always derives, so legacy runs get structured evidence too. `proposals.json` gains `key` and `details` (§7.3) per proposal, and `hubs`.

`build_cases` makes one group per dropped story (`mg:<drop>`, options ordered by m), and one case per split or overlap, keyed by proposal key. Each case carries a strength (top m, bundled score or mean o), `conflicts` and `order`.

`diff_runs(a, b)`: stories by ID (added, removed, edited, renamed = a removed hash under a new ID); proposals by key (new, gone, changed by |Δ| ≥ 0.05 or a borderline flip); decisions naming a changed story.

### 4.4 Re-run

New CLI: `run … --estimate-json`, a dry run that writes nothing (no call, no key, no run directory; works with `--resume`), prints one JSON line: `to_ask, cached, usd, cap_usd, over_cap, snapshot_sha256`; `run … --progress-jsonl PATH` appends the §7.4 events; `key --json` prints a `KeyStatus` via `JevClient.key_info()`, the free `GET /api/v1/key`.

`JobManager`:

1. **Estimate:** spawn `--estimate-json` (120 s timeout); keep argv and source hash for 15 minutes.
2. **Start:** check in order the hash is unchanged (409 `estimate_stale`), `confirm == "spend"`, estimate ≤ `max_usd` ≤ min(`ui_max_usd`, remaining) (422 `cap_invalid`), and no job is running (409 `job_running`); then spawn `[*cli, "run", …, "--max-usd", X, "--out", "<runs>/ui", "--progress-jsonl", P]` in a new session.
3. **Poll:** each second, write progress, the redacted log tail and exit code to `job.json`.
4. **Cancel:** SIGINT to the process group, SIGTERM after 20 s; the run stays resumable.
5. **Restart:** re-attach by pid; if dead, read `run.json`, else mark `lost`.

`cli` defaults to `[sys.executable, "-m", "tesselint"]` (add `__main__.py`); tests set `TESSELINT_UI_CLI`. The browser never sends an argv or a path.

### 4.5 Input v1

`inputv1.py` holds the converter and validator of INPUT.md §3–§4; the schema ships as package data, tested byte-equal to `schemas/`. A JSON source is copied byte for byte to `snapshot.json`; `load_snapshot` loads whichever exists. `parse_snapshot` validates, then maps `statement`→`core`, `details`→`subs` and `source_line`→`line`; errors follow INPUT.md §4 and exit 4. New commands: `import SOURCE.md --out F`, `export F`, `parse --source F.json`, `synth --format json` (sets `x-synthetic`). New dependencies (log them): `starlette`, `uvicorn`, `jsonschema`.

## 5. Frontend architecture

**Layout:** `web/src/lib/`: `router`, `api`, `types`, `hotkeys`, `phrases`, `matrix`, `cases`, `trust`, `theme`; `web/src/components/`: Header, TrustBanner, StoryLink, SideBySide, DecisionBar, Heatmap, PairDrawer, JsonModal; `web/src/views/` and `web/e2e/`.

**Dependencies:** `react ^19.3`, `@tanstack/react-query ^5`, `tailwindcss` with `@tailwindcss/vite ^4.3`, `vite ^8.3`, `@vitejs/plugin-react ^6`, `typescript ^7` (only `tsc --noEmit`), `vitest ^5`, `@testing-library/react ^16`, `jsdom`, `@playwright/test ^1.63` and `@axe-core/playwright`. Log any other.

**Build.** `npm --prefix web run build` runs `tsc --noEmit`, then `vite build --outDir ../src/tesselint/server/static --emptyOutDir`, then `scripts/stamp.mjs`.

The stamp writes `build.json` `{source_hash}`: sha256 over `web/src/**`, `index.html`, the lockfile and `vite.config.ts` in sorted POSIX-path order, each fed as `path + "\0" + bytes + "\0"`. The output (about 300 KB) is committed, so `serve` needs no Node; pytest recomputes the hash and fails on stale assets; `.gitattributes` marks it `linguist-generated -diff`.

**Routing and state.** An in-house History API router: `parseLocation()` and `href(route)` map a discriminated union with typed search params and must round-trip; the server returns `index.html` outside `/api` and `/assets`. Server data lives in TanStack Query (jobs poll each second), view state in the URL, the theme in `localStorage` (try/catch). Decisions are optimistic, roll back on error, and are replaced by the response.

**Heatmap.** One `ImageData` at 1 px per cell scaled with `imageSmoothingEnabled=false`, plus an overlay canvas for hover, focus, brush and boundaries; arithmetic hit tests, no DOM per cell; pure helpers `packIndex`, `orderBy`, `epicAggregate`, `rgbaFor`, `hitTest`. Colour: the dataviz blue ramp, 13 steps `#cde2fb`→`#0d366b`, reversed in dark mode; `null` uses `--cell-missing`, the diagonal `--surface-2`, o < t is drawn at 25 % alpha. The legend marks t; tooltips use `textContent`.

**Phrases.** Tokens match `/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu`, lower-cased; DP repeatedly extracts the longest common run of ≥ 3 tokens, skipping stopword-only runs (above 250k DP cells, shared 4-grams). Spans carry a run index so hovering one lights its twin; they render as `<mark>`, never via `innerHTML`.

**Tokens (light/dark):** `--bg` `#fefefe`/`#0e0d0f`, `--surface` `#ffffff`/`#17161a`, `--surface-2` `#f4f4f2`/`#1f1e22`, `--border` `#e4e4e0`/`#2c2b30`, `--text` `#0e0d0f`/`#f4f4f2`, `--text-2` `#52514e`/`#c3c2b7`, `--muted` `#6b6a66`/`#9a998f`, `--accent` `#10b981` (text `#047857`/`#34d399`), `--focus` `#2a78d6`, `--mark` `#fff1b8`/`#5c4a00`. Status colours always carry an icon and label: accept `#0ca30c` ✓, reject `#d03b3b` ✗, defer `#fab219` ⏸, stale `#ec835a` ⟳. `rounded-lg` cards, no pills, `tabular-nums`, monospace IDs.

**Accessibility.** WCAG 2.2 AA, 2 px focus ring, `aria-live`, focus-trapping dialogs, reduced motion, a Table twin for the heatmap.

**Budgets (176 stories).** JS ≤ 250 KB gzip; payload ≤ 2 MB; open ≤ 2 s; matrix paint ≤ 500 ms (≤ 1.5 s at 500); hover ≤ 50 ms; reorder ≤ 300 ms; next case ≤ 100 ms; save ≤ 200 ms p95; re-derive ≤ 2 s.

## 6. Tests first

**Fixture:** `tests/fixtures/ui/build_fixture.py` (committed, deterministic, no Jev):

1. runs `synth --n 24 --seed 42 --format json`;
2. runs `pipeline.run` on `FakeJev` with a graded oracle (text-hashed probabilities: 0.55–0.97 planted, 0–0.6 otherwise) into `runs/mini/20260101-090000`;
3. edits 2 stories, adds 1, removes 1, and writes `runs/mini/20260102-090000`;
4. writes `source.v1.json` (one edit further), `decisions.seed.json` and `manifest.json` (every expected count);
5. normalises the volatile fields;
6. asserts a group with ≥ 2 options, a hub with a split conflict, a borderline merge and ≥ 2 overlaps.

A slow test proves rebuilds are byte-identical.

**pytest:**

- **inputv1:** schema and cross-field rules; 12 mutations with exact messages and exit 4; byte-exact snapshot; equal v1 and Markdown hashes on `section4_2026-09-26.md`; round trip.
- **core:** `proposal_key`; `derive` writes nothing and equals `report`; `details` match `evidence`; new `run.json` fields; `--estimate-json` makes no directory; `key --json` never prints the key.
- **runindex:** refs, collisions, inference, latest, previous.
- **decisions:** apply, undo, rev; a failed `os.replace` keeps the old file; every status; reload after external edits; unreadable file blocks writes; the lock.
- **diff:** equals the manifest. **api:** §7.3 shapes; 404, 409, 415, 422.
- **security:** guard matrix; hostile refs (`..`, `%2e%2e`, absolute, `a~..~b`) and static traversal give 404; no `sk-or-` in any response, log or asset (`OPENROUTER_API_KEY=sk-or-canary`); `tesselint.server` never loads `jev`, `httpx` or `dotenv`; a socket guard fails non-loopback connects; source and run hashes unchanged.
- **jobs** (fake CLI `tests/fakes/fake_cli.py`: `ok|cap|auth|slow|bad_input`): one job at a time; stale estimates; caps; SIGINT on cancel; re-attach; no `run` without an estimate.
- **static, serve:** hash and token placeholder; non-loopback host exits 2; `load_dotenv` never called.

**Vitest:** router round trip; phrases; matrix pack, orders, aggregate, colour; case filters; hotkeys; optimistic reducer; DecisionBar, SideBySide, TrustBanner; heatmap hit tests.

**Playwright** (`channel: 'chrome'`, one worker): `webServer` runs `uv run tesselint serve --runs tests/fixtures/ui/runs --state web/e2e/.state --source tests/fixtures/ui/source.v1.json --port 8799 --no-open` with the fake CLI and a canary key; the seed resets before each test.

- **E1 Overview:** opens in ≤ 2 s; the banner says "not decisions"; the tiles equal the manifest; the run shows `synthetic`
- **E2 Keyboard:** `j 2 a r d`, a note, then `u` yield exactly the expected `decisions.json` and rev
- **E3 Supersede:** accepting option 1 supersedes option 2
- **E4 Conflict:** the hub's case shows its split; `c` reaches it
- **E5 Survival:** run 1's decisions, shown on run 2, match the manifest's current and stale counts, with a word diff; none are lost
- **E6 Matrix:** hover shows the right IDs in ≤ 50 ms; a click drills in; cluster order changes a known pixel (`getImageData`); the slider fades cells; the drawer has a `<mark>` per side
- **E7 Navigation:** neighbours are ≤ 2 interactions away and sorted by o; `/` then Enter opens a story
- **E8 Diff:** equals the manifest
- **E9 Re-run:** Start stays disabled until ticked; progress ends at a listed run; the `cap` and `auth` messages show; the fake's argv has the estimate before `--max-usd`
- **E10 Export:** the Markdown has IDs, titles and hashes; the JSON validates against `schemas/decisions-export.v1.schema.json`; the write creates both files
- **E11 Thresholds:** merge at 0.9 gives the manifest count; the run dir is unchanged
- **E12 Security:** no `sk-or-`; a POST without the token, or from a foreign Origin, gets 403
- **E13 Accessibility:** no serious axe issue in either theme; visible focus; the largest case fits 1440 × 900
- **E14 Source:** source and fixture hashes unchanged

**Real-data check** (opt-in, on a real backlog; spend reported):

1. Convert the backlog, which stays read-only, into `runs/inputs/real.v1.json` (INPUT.md §5, or `import` for Markdown).
2. `parse --source` exits 0, and every rendered text hash equals the Markdown import's.
3. `--estimate-json` shows `to_ask` ≈ 0 once the backlog has been run.
4. `run --source … --max-usd 0.05` ends `complete`, ≤ US$0.05, `kind: real`.
5. `serve` plus `e2e/real.spec.ts` (`@real`, opt-in): the backlog's story count, the derived case count, matrix paint ≤ 500 ms, no `sk-or-`.

## 7. Binding interfaces

### 7.1 Python

```python
# additive, in existing modules
def proposal_key(kind: ProposalKind, target: str | None, stories: Sequence[str]) -> str
Proposal.key: str = ""; Proposal.details: Mapping[str, Any] | None = None   # filled by propose()
def parse_snapshot(snapshot: Snapshot) -> ParseResult; def epic_names(snapshot: Snapshot) -> list[tuple[str, str]]
async def JevClient.key_info(self) -> KeyInfo | None                         # KeyInfo(usage_usd, limit_usd)
async def run(cfg, client, *, progress=None, events: Callable[[Mapping[str, Any]], None] | None = None) -> Path
def derive(run_dir: Path, th: Thresholds) -> Derived  # Derived(stories, verdicts, clusters, atomic, proposals, thresholds)
# inputv1.py
load_v1(path) -> dict; validate_v1(doc) -> list[InputProblem]  # InputProblem(pointer, story_id, message, severity)
to_parse_result(doc) -> ParseResult; import_markdown(source, *, section="## 4.") -> dict; export_markdown(doc) -> str
# tesselint.server
ServeConfig(host, port, runs_root, state_dir, sources, cli, ui_max_usd, token, static_dir); create_app(cfg) -> Starlette
LocalGuard(app, *, port, token); scan_runs(runs_root, max_depth=4) -> list[RunEntry]  # RunEntry(ref, path, meta, kind, kind_inferred, dry_run)
RunStore(runs_root, capacity=3): entries(include_dry), bundle(ref), matrix(ref), proposals(ref, th), pair(ref, a, b)
build_cases(proposals, order) -> list[dict]; diff_runs(a: Derived, b: Derived) -> dict
DecisionStore(path): read(), apply(run_ref, derived, snapshot_sha256, changes) -> rev, undo() -> rev, views(derived, current_source)
build_export(views, run, *, include_stale) -> dict; export_markdown_doc(doc) -> str
JobManager(state_dir, cli, runs_root, ui_max_usd): estimate(source, scope), start(estimate_id, max_usd, key), get(id), cancel(id), active()
```

### 7.2 API

Errors are `{"error":{"code","message"}}`; unversioned, since its only client ships in the same wheel; POSTs need the token. For decisions the browser sends only keys; the server checks each against the run and fills in hashes, texts and titles.

**GET:** `/api/health` → `{version, build, sources: {id, path, format, exists}[], ui_max_usd}`; `/api/runs?include=dry` → `{latest, runs: RunSummary[]}`; `/api/runs/{ref}` → `RunBundle`; `/api/runs/{ref}/matrix` → `MatrixPacked`; `/api/runs/{ref}/proposals?merge&cluster&split` → `ProposalsResponse`; `/api/runs/{ref}/pairs/{a}/{b}` → `PairDetail`; `/api/diff?a&b` → `RunDiff`; `/api/decisions?run&merge&cluster&split` → `DecisionsResponse`; `/api/export?run&format&include_stale` → a file; `/api/key` → `KeyStatus`; `/api/jobs` and `/api/jobs/{id}` → `Job`.

**POST:** `/api/decisions {run, thresholds?, changes}` → `DecisionsResponse`; `/api/decisions/undo {run}` → `DecisionsResponse`; `/api/exports {run, include_stale}` → 201 `{md_path, json_path}`; `/api/estimates {source_id, tier?, epics?, resume?}` → 201 `Estimate`; `/api/jobs {estimate_id, max_usd, confirm:"spend"}` → 202 `Job`; `/api/jobs/{id}/cancel` → 202 `Job`.

**Errors:** 404 `run_not_found`; 409 `job_running`, `estimate_stale`, `nothing_to_undo`; 422 `bad_threshold`, `unknown_key`, `cap_invalid`; 502 `cli_failed`.

### 7.3 TypeScript (`web/src/lib/types.ts`, all exported)

```ts
type S = string; type N = number; type B = boolean; type Kind = "merge" | "overlap" | "split"; type Action = "accept" | "reject" | "defer";
type Relation = "duplicate" | "A_contains_B" | "B_contains_A" | "partial_overlap" | "same_area_distinct" | "unrelated"; type Probs = Partial<Record<Relation, N>>;
type Thresholds = Record<S, N>;  // the eight models.Thresholds fields
interface RunSummary { ref: S; kind: "real" | "synthetic"; kind_inferred: B; dry_run: B; status: S; source: { path: S | null; format: "md" | "json" | null };
  tier: S | null; n_stories: N; finished_at: S | null; cost_live_usd: N; cost_cached_usd: N; snapshot_sha256: S; proposals: Record<Kind, N> }
interface StoryRow { id: S; epic: S; title: S; tag: S; words: N; text_hash: S; text: S; atomic: N | null; order: N }
interface Edge { a: S; b: S; overlap: N; merge: N; relation: Relation }
interface RunBundle { run: RunSummary; thresholds: Thresholds; epics: { id: S; name: S }[]; stories: StoryRow[]; tombstones: { id: S; title: S; target: S | null }[];
  clusters: { id: S; members: S[]; mean_overlap: N; edges: Edge[] }[]; previous: S | null; calibration: Record<S, N | B> | null; calibration_from: S | null;
  source_status: { source_id: N | null; same_snapshot: B; edited: S[]; added: S[]; removed: S[]; problems: S[] } }
// upper triangle, row-major, i < j in document order: k = i*n - i*(i+1)/2 + (j-i-1); rel indexes rel_order with ids[i] as A
interface MatrixPacked { ids: S[]; o: (N | null)[]; m: (N | null)[]; rel: (N | null)[]; rel_order: Relation[] }
interface Base { id: S; key: S; target: S | null; stories: S[]; decisive_value: N; borderline: B; conflicts: S[] }
interface MergeDetails { relation: "duplicate" | "containment"; keep: S; drop: S; m: N; o: N; fwd: Probs; swap: Probs | null; cluster_keep: S | null; cluster_drop: S | null;
  cos: N | null; atomic_keep: N | null; atomic_drop: N | null; related: Edge[]; alternatives: S[] }
interface OverlapDetails { cluster: S; members: S[]; mean_o: N; edges: Edge[]; related: Edge[] }
interface SplitDetails { story: S; bundled: N; words: N; n_subs: N; cluster: S | null; reaches: S[]; keeps: S[] }
type Proposal = (Base & { kind: "merge"; details: MergeDetails }) | (Base & { kind: "overlap"; details: OverlapDetails }) | (Base & { kind: "split"; details: SplitDetails });
interface Case { key: S; kind: Kind; anchor: S; proposals: S[]; strength: N; borderline: B; epics: S[]; conflicts: S[]; order: N }
interface ProposalsResponse { thresholds: Thresholds; proposals: Proposal[]; cases: Case[]; hubs: { story: S; keeps: S[]; split: S | null }[] }
interface PairDetail { a: S; b: S; overlap: N; merge: N; relation: Relation; fwd: Probs; swap: Probs | null; cos: N | null; proposals: S[] }
interface Decision { key: S; kind: Kind; target: S | null; stories: S[]; action: Action | null; note: S; text_hashes: Record<S, S>; titles: Record<S, S>; run: S; decided_at: S }
interface DecisionView extends Decision { status: "current" | "stale" | "missing"; changed: S[]; proposed: B; superseded_by: S | null; applied: B | null; old_texts?: Record<S, S> }
interface DecisionsResponse { rev: N; decisions: DecisionView[] }
interface DecisionChange { key: S; action?: Action | "clear"; note?: S }
interface RunDiff { added: S[]; removed: S[]; renamed: [S, S][]; edited: { id: S; old_text: S; new_text: S }[]; new: S[]; gone: S[]; changed: S[]; decisions_to_review: S[] }
interface KeyStatus { present: B; usage_usd: N | null; limit_usd: N | null; remaining_usd: N | null; error: S | null }  // = key --json
interface Estimate { id: S; to_ask: N; cached: N; usd: N; eta_s: N; command: S; expires_at: S; key: KeyStatus; max_usd_allowed: N }
interface Job { id: S; state: "running" | "done" | "failed" | "cancelled" | "lost"; exit_code: N | null; message: S | null; run_ref: S | null; max_usd: N; spent_usd: N;
  log_tail: S[]; phases: { name: S; done: N; total: N }[] }
```

### 7.4 File formats

**`decisions.json`**

```
{"format":"tesselint/decisions","version":1,"rev","decisions":{key: Decision+"texts"},
 "history":[{"rev","at","undo_of"?,"changes":[{"key","before","after"}]}]}
```

**Export JSON** (`schemas/decisions-export.v1.schema.json`):

```
{"format":"tesselint/decisions-export","version":1,"run":{"ref","snapshot_sha256"},
 "items":[{"order","key","action":"merge"|"split"|"untangle",
           "stories":[{"id","title","text_hash","text"}],
           "note","evidence","status","warnings","instruction"}]}
```

**Export Markdown:** a header (run, snapshot), then "Apply in order; if a story's text no longer matches, skip it and report its ID." Each item is a `## <n>. <Action>` section with IDs, hashes, why (m, o, borderline), note and a fixed **Do** line: merge "Fold US-4.4's distinct details into US-3.3, then replace its line with `- **US-4.4 <title>** — merged into US-3.3`"; split "Keep US-3.5 for one part; give the others new, unused IDs"; untangle "Make each boundary explicit; merge nothing unless a merge item says so".

**Jobs:** `job.json` is a `Job` plus `pid` and `argv`; `progress.jsonl` events: `run_dir`; `estimate` (`to_ask`, `usd`); `phase` (`phase`, `done`, `total`, `spent_usd`; at most one per second); `done` (`status`).

## 8. Build order, risks, out of scope

**Build order** (each step starts from failing tests): input v1 → core additions and CLI flags → fixtures → server, guard, index, read API → decisions, diff, export → jobs → scaffold and Overview → Review → Matrix → Story, Epic, Clusters → Runs and Re-run → Export → E2E, a11y, performance, commit the assets → final test.

**Risks:** (1) stale assets: the hash test; (2) key drift: stale and orphaned decisions surface, never deleted; (3) spend: estimate, confirm, caps, balance check, one spawn path, fake CLI; (4) over-trusting Jev: banner and band chips; (5) 500 stories: caches, packed matrix, brush zoom; (6) new tool majors: lockfile, fall back one major and log it.

**Out of scope (v1):** editing the source; hosting, accounts, share links, mobile; label, eval or synth from the UI; prompt changes; seriation, SSE, redo; auto-committing decisions.

**Open questions:** (1) commit `decisions.json` to git? (2) default v1 `status` to `live` (INPUT.md §6)?

## 9. Decisions (2026-09-26)

- **Open question 1:** `decisions.json` stays in the gitignored state dir (`.tesselint/`). It holds backlog text; the export (`.md`/`.json`) is the artefact that travels to whoever edits the source.
- **Open question 2:** v1 `status` defaults to `live` when absent (the schema keeps it optional; the validator fills the default).
- **Implementation:** tests first. The only paid Jev use is the real-data check (cap US$0.05).
