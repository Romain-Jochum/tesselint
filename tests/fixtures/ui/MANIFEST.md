# UI fixture manifest (binding contract)

`tests/fixtures/ui/build_fixture.py` builds a deterministic fixture (no Jev, no network) as `docs/ui/DESIGN.md` §6 describes, and writes `tests/fixtures/ui/manifest.json`. Python tests, Vitest and Playwright read expectations from this manifest instead of hard-coding counts, so the builder is the single source of truth. Every key below is required.

## Files

- `tests/fixtures/ui/runs/mini/20260101-090000/` (run 1) and `tests/fixtures/ui/runs/mini/20260102-090000/` (run 2): full run directories (DESIGN §9 of `docs/DESIGN.md` plus the new `run.json` fields of UI DESIGN §4.2), volatile fields normalised (timestamps fixed, latencies 0, gen ids `gen-fixture-N`).
- `tests/fixtures/ui/source.v1.json`: the v1 input, one edit further than run 2.
- `tests/fixtures/ui/decisions.seed.json`: a `decisions.json` (UI DESIGN §7.4) with decisions made on run 1.
- `tests/fixtures/ui/manifest.json`: the expectations below.

## manifest.json

```json
{
  "format": "tesselint/ui-fixture-manifest",
  "version": 1,
  "runs_root": "tests/fixtures/ui/runs",
  "source": "tests/fixtures/ui/source.v1.json",
  "latest": "mini~20260102-090000",
  "previous": "mini~20260101-090000",
  "runs": {
    "<ref>": {
      "kind": "synthetic",
      "n_stories": 0, "n_epics": 0, "n_tombstones": 0,
      "proposals": {"merge": 0, "overlap": 0, "split": 0},
      "cases": {"total": 0, "merge_groups": 0, "split": 0, "overlap": 0},
      "first_cases": ["<case key>", "… the first 5 case keys in default order (open, sort=strength)"],
      "hubs": ["<story id>"],
      "multi_option_group": "<case key of a merge group with >= 2 options>",
      "multi_option_group_options": ["<proposal key>", "… in option order (m desc)"],
      "hub_story": "<story id kept by >= 3 merges>",
      "hub_split_key": "<split proposal key on hub_story>",
      "borderline_merge_key": "<proposal key>",
      "overlap_keys": ["<proposal key>", "…"],
      "pair": {"a": "<id>", "b": "<id>", "overlap": 0.0, "merge": 0.0, "relation": "<Relation>"},
      "neighbours_of": {"story": "<id>", "top5": ["<id>", "… by o desc, o >= 0.4"]},
      "merges_at_merge_0_9": 0,
      "epic_cell": {"row": "E1", "col": "E2", "pairs": 0, "pairs_at_or_above_t": 0, "t": 0.7}
    }
  },
  "diff": {
    "a": "mini~20260101-090000", "b": "mini~20260102-090000",
    "added": [], "removed": [], "edited": [], "renamed": [],
    "new": [], "gone": [], "changed": [], "decisions_to_review": []
  },
  "seed_decisions": {
    "file": "tests/fixtures/ui/decisions.seed.json",
    "total": 0,
    "on_latest": {"current": 0, "stale": 0, "missing": 0, "orphaned": 0},
    "stale_keys": [], "missing_keys": []
  },
  "source_vs_latest": {"edited": [], "added": [], "removed": []}
}
```

`first_cases` is computed on that run with `decisions.seed.json` applied and the default Review filter: `status=open` (undecided plus stale; a deferred case counts as decided), `sort=strength`.

`runs` holds one entry per ref (both runs). Refs use `~` for `/` (UI DESIGN §4.2). Lists are sorted where the DESIGN does not define an order. The builder asserts the DESIGN §6 properties (a group with >= 2 options, a hub with a split conflict, a borderline merge, >= 2 overlaps, >= 1 stale and >= 1 missing seed decision) and fails loudly otherwise.
