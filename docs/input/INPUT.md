# Input format: stories v1

A canonical, validated input any backlog converts into, without changing any text sent to Jev. Schema: `schemas/stories.v1.schema.json` (draft 2020-12); converter and validator: `src/tesselint/inputv1.py`.

## 1. Candidates

Scores 1 (poor) to 3 (good); round-trip means v1 ↔ today's Markdown with identical `parse_section` output.

| Format | Convert | Validate | Git diff | Edit | Round-trip | Note |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| **JSON + JSON Schema** | 3 | 3 | 2 | 2 | 3, proven | **Chosen** |
| TOML | 2 | 2 | 3 | 3 | 3 | Stdlib reads, cannot write |
| YAML | 2 | 2 | 3 | 3 | 3 | Colon-rich tags (`MVP: …`) need quoting; LLM indentation errors |
| Strict Markdown | 2 | 1 | 3 | 3 | 3 | Validation is the regex we leave; stays as legacy input and `export` view |
| CSV | 3 | 1 | 2 | 2 | 1 | Lists need a sub-encoding; import source only |
| Gherkin-like | 1 | 1 | 3 | 2 | 1 | Forces Given/When/Then the source lacks |
| ReqIF, Jira/ADO JSON | 1 | 2 | 1 | 1 | 1 | XML or vendor bodies; import sources only |

JSON wins on strict validation with pointer-level errors, reliable LLM output and a stdlib parser. Editing goes through `export` to Markdown and `import` back, which round-trips exactly.

## 2. Schema v1

Principle: **text fields hold the source's words verbatim**, markup, story refs, `(G10)` codes and inline tags included. Cleaning happens only at render time, so conversion cannot move the Jev text or its cache key.

**Document.** `format: "tesselint/stories"`, `version: 1`, `source` (`path` or URL, `converter`: `tesselint import/<v>` or `llm:<model>`; optional `section`, `sha256` of the bytes converted, `line_offset`), `generated_at` (RFC 3339), `epics[]` (`id`, `name` verbatim from the heading, optional `source_line`), `stories[]`. Optional: `render` (default `md-clean/1`, §3) and `priorities`, a closed vocabulary in rank order (here `MVP, MVP+, Lite, Later, Not now, decision`).

**Story.**

| Field | Required | Meaning (legacy `Story` field) |
| :-- | :-- | :-- |
| `id` | yes | Source ID verbatim (`id`) |
| `epic` | yes | Epic it sits under, not the one its ID suggests (`epic`) |
| `title` | yes | Verbatim minus tag and trailing period; no brackets (`title`) |
| `status` | yes | `live` or `removed` |
| `statement` | if live | Core sentence verbatim, `""` if none (`core`) |
| `details` | no, `[]` | Sub-items verbatim, one line each (`subs`) |
| `tag` | no | Bracket text verbatim with qualifiers (`tag`) |
| `priority` | no | Tag's leading keyword, from `priorities` |
| `merged_into` | if removed | Target ID or `null`; forbidden when live |
| `removed_reason` | no | Text after `merged into X:` |
| `references`, `source_line` | no | Derived cited IDs; 1-based line in `source.path` |

**Rules.** IDs match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, are unique across live and removed stories, and are never renumbered or reused. Text fields are single trimmed lines. Array order is meaningful: epics in narrative order, stories in source order. The validator adds cross-field rules: unique IDs, known `epic`, `priority` in `priorities`, `merged_into` chains ending at a live story. It warns on a live story with neither statement nor details, an epic without live stories, identical rendered texts, unresolved references.

**Extensions.** Any object may carry `x-*` keys (`x-jira-key`); other unknown keys are errors, so typos fail. Breaking changes mean `version: 2`.

```json
{"id": "US-4.7", "epic": "E4", "title": "Repair queue", "tag": "MVP", "priority": "MVP", "statement": "",
 "details": ["Broken tools wait here, oldest first.", "A volunteer sees why each one is waiting."],
 "status": "live", "source_line": 70}
```

## 3. Render contract and cache continuity

Profile `md-clean/1` is today's `render()`, with `render.clean` as the normative cleaner:

1. Non-empty `statement`: head = `clean(title + ". " + statement)`, bullets = `details`.
2. Else non-empty `details`: head = `clean(title + ". " + details[0])`, bullets = `details[1:]`.
3. Else head = `clean(title + ".")`.
4. Full text = head plus `"- " + clean(d)` for each bullet that cleans to non-empty, joined by `\n`; core mode = head.

Nothing else reaches Jev. Changing `clean` or these steps needs a new profile, which then enters the cache key.

**Result** (real §4, 26 Sep 2026): **176/176 full-text hashes and 176/176 core texts identical**, from an independent implementation of these steps after a JSON write/read. Also: 0 schema errors, 1 warning (a story with no text), every text field found verbatim in the source, v1 → Markdown → `parse_section` identical for 176 stories and 3 tombstones, synthetic n=60 at 60/60, 12/12 deliberately broken conversions rejected.

Verbatim storage is why: lifting `(G10)` out of a title turns today's `Title . Core` into `Title. Core`, a new hash.

## 4. CLI integration

- `--source` dispatches on suffix: `.json` is validated, then mapped to `Story` (`statement`→`core`, `details`→`subs`, `tag or ""`) and `Tombstone`; `.md` keeps the legacy parser. Downstream is untouched.
- A JSON source is copied byte for byte to `runs/<id>/snapshot.json` plus `snapshot.sha256`; `--resume` loads whichever snapshot exists.
- `tesselint import SOURCE.md --out stories.json` runs the deterministic converter; `tesselint export stories.json` writes legacy Markdown for editing (`US-`/`E<n>` IDs only); `tesselint parse --source stories.json` validates and counts.
- Each problem is one stderr line, `stories.json: $.stories[12] (US-2.3).status: 'alive' is not one of ['live', 'removed']`, then `tesselint: error: 3 invalid field(s) in stories.json; fix them first`, exit 4. Malformed JSON: one line with line and column. Warnings print; exit 0.
- Add `jsonschema`; ship the schema as package data (a test keeps it equal to `schemas/`); check `generated_at` with `datetime.fromisoformat`.
- `Story.line` becomes the absolute `source_line` (today counted from `## 4.`); cache keys are unaffected.
- `tesselint synth --format json` emits v1 from its items; `truth.json` is unchanged.

## 5. Convert another format with an LLM

A Markdown backlog in the `### E<n>` / `- **US-<n>.<m> Title.** …` shape needs no LLM: `tesselint import` converts it word for word. For anything else (a tracker export, a spreadsheet, a requirements document), paste the prompt below into a capable LLM, followed by your backlog, and save the answer as `stories.json`.

```text
Convert the backlog at the end of this message into one JSON document in the "stories v1"
format. Reply with the JSON only, no prose and no code fence.

The document:
- "format": "tesselint/stories", "version": 1, "render": "md-clean/1"
- "source": {"path": "<the backlog's file name>", "converter": "llm:<your model name>"}
- "generated_at": the current UTC time, for example "2026-09-27T12:00:00Z"
- "epics": one {"id", "name"} per group heading, in order. Use the heading's own ID (such as
  E7), else E1, E2, ... in order; "name" is the rest of the heading.
- "stories": every story, in source order.

Each story:
1. "id": the source ID, copied exactly. Never renumber or fill gaps. If the source has no IDs,
   mint S-001, S-002, ... in order and add "x-minted-id": true.
2. "epic": the ID of the heading the story sits under, even if the story's own ID points to
   another epic.
3. "title": copied exactly, without a bracketed tag or a trailing period. Keep codes such as
   (G10) or (P2).
4. "tag": the text of the first bracket after the title, copied exactly (for example
   "MVP, pilot only"). Leave any other brackets in the text.
5. "statement": the rest of the story's first line, copied exactly with its markup, or "" if
   there is none. Never promote a detail, write a new sentence or add "As a ...".
6. "details": each following line up to the next story, heading or ---, trimmed and in order:
   bullets at any depth (flattened), indented paragraphs and trailing prose. A table row
   becomes its non-empty cells joined by "; " (keep the header row, skip the separator row).
   Skip blank lines.
7. "status": "live", or "removed" for a story marked as merged or removed. A removed story
   also gets "merged_into" (the target ID, or null) and "removed_reason" when one is given.
8. Never invent, fix, translate, merge, split or reword text. Leave out "priority" and
   "references".

If the backlog is long, convert one epic per message and join the "epics" and "stories" lists.

The backlog:
```

Then check the result before you run it:

- `tesselint parse --source stories.json` validates it: one line per invalid field and exit 4, or the story and tombstone counts. Warnings print and exit 0.
- Compare the counts of stories, removed stories and epics with the source.
- Spot-check that titles, statements and details are copied word for word. Jev sees the text, so a reworded story is a different story.

## 6. Open questions

1. Record nesting depth in `details` (never rendered)?
2. Should prose after a story's last bullet stay attached to it, as the parser does now?
3. Default `status` to `live` for easier hand editing?
4. `parse.py` keeps a tombstone's tag in its title (synth `US-1.90`); v1 splits it out.
