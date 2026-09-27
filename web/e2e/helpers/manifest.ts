// The fixture manifest (tests/fixtures/ui/MANIFEST.md, binding): every count and key a spec expects
// comes from here, never from a hard-coded number. It is read lazily so `playwright test --list`
// works before the builder has produced it.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { MANIFEST_PATH, REPO } from "./paths.ts";

export interface RunExpect {
  kind: "real" | "synthetic";
  n_stories: number;
  n_epics: number;
  n_tombstones: number;
  proposals: { merge: number; overlap: number; split: number };
  cases: { total: number; merge_groups: number; split: number; overlap: number };
  /** The first 5 case keys in default order (open, sort=strength), with the seed decisions applied. */
  first_cases: string[];
  hubs: string[];
  multi_option_group: string;
  multi_option_group_options: string[];
  hub_story: string;
  hub_split_key: string;
  borderline_merge_key: string;
  overlap_keys: string[];
  pair: { a: string; b: string; overlap: number; merge: number; relation: string };
  neighbours_of: { story: string; top5: string[] };
  merges_at_merge_0_9: number;
  epic_cell: { row: string; col: string; pairs: number; pairs_at_or_above_t: number; t: number };
}

export type DiffSection = "added" | "removed" | "edited" | "renamed" | "new" | "gone" | "changed" | "decisions_to_review";

export interface Manifest {
  format: "tesselint/ui-fixture-manifest";
  version: 1;
  runs_root: string;
  source: string;
  latest: string;
  previous: string;
  runs: Record<string, RunExpect>;
  diff: { a: string; b: string } & Record<DiffSection, unknown[]>;
  seed_decisions: {
    file: string;
    total: number;
    on_latest: { current: number; stale: number; missing: number; orphaned: number };
    stale_keys: string[];
    missing_keys: string[];
  };
  source_vs_latest: { edited: string[]; added: string[]; removed: string[] };
}

let cached: Manifest | null = null;

export function loadManifest(): Manifest {
  if (!cached) {
    const doc = JSON.parse(readFileSync(MANIFEST_PATH, "utf8")) as Manifest;
    if (doc.format !== "tesselint/ui-fixture-manifest" || doc.version !== 1) {
      throw new Error(`${MANIFEST_PATH} is not a v1 UI fixture manifest`);
    }
    cached = doc;
  }
  return cached;
}

export function runOf(manifest: Manifest, ref: string): RunExpect {
  const run = manifest.runs[ref];
  if (!run) throw new Error(`manifest has no run ${ref}`);
  return run;
}

export const latestRun = (manifest: Manifest): RunExpect => runOf(manifest, manifest.latest);

/** A run directory from its ref (DESIGN §4.2: `/` → `~`). */
export function runDir(manifest: Manifest, ref: string): string {
  return resolve(REPO, manifest.runs_root, ...ref.split("~"));
}

/** The Review case a proposal belongs to (DESIGN §4.3): `mg:<drop>` for merges, else the key itself. */
export function caseKeyOf(proposalKey: string): string {
  const merge = /^merge:[^>]+>(.+)$/.exec(proposalKey);
  return merge ? `mg:${merge[1]}` : proposalKey;
}

/** A diff entry as the UI keys it (TID.diffList rows): IDs and proposal keys as is, renamed pairs as `old>new`. */
export function diffKey(entry: unknown): string {
  if (typeof entry === "string") return entry;
  if (Array.isArray(entry)) return entry.map(String).join(">");
  if (entry && typeof entry === "object" && "id" in entry) return String((entry as { id: unknown }).id);
  if (entry && typeof entry === "object" && "key" in entry) return String((entry as { key: unknown }).key);
  throw new Error(`unrecognised diff entry ${JSON.stringify(entry)}`);
}
