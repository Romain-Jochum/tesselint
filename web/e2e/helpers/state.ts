// State-dir reset and file checks (docs/ui/DESIGN.md §6 "the seed resets before each test"; §3
// decisions store; §7.4 decisions.json and job.json).
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import type { Decision } from "../../src/lib/types.ts";
import { DECISIONS_PATH, EXPORTS_DIR, JOBS_DIR, SEED_PATH, STATE_DIR } from "./paths.ts";

/** A stored record (§7.4 `decisions: {key: Decision + "texts"}`). */
export type StoredDecision = Decision & { texts?: Record<string, string> };

export interface DecisionsFile {
  format: "tesselint/decisions";
  version: 1;
  rev: number;
  decisions: Record<string, StoredDecision>;
  history: { rev: number; at: string; undo_of?: number; changes: { key: string; before: unknown; after: unknown }[] }[];
}

/**
 * Copy decisions.seed.json into the state dir and drop exports (§6). The server re-reads
 * decisions.json when its mtime or size changes (§3 "hand edits win"), so no restart is needed.
 */
export function resetState(): void {
  mkdirSync(STATE_DIR, { recursive: true });
  copyFileSync(SEED_PATH, DECISIONS_PATH);
  rmSync(EXPORTS_DIR, { recursive: true, force: true });
}

export const readSeed = (): DecisionsFile => JSON.parse(readFileSync(SEED_PATH, "utf8")) as DecisionsFile;
export const readDecisionsFile = (): DecisionsFile => JSON.parse(readFileSync(DECISIONS_PATH, "utf8")) as DecisionsFile;

/** Every regular file under dir (or dir itself when it is a file), POSIX-relative, sorted. */
function files(root: string): string[] {
  if (statSync(root).isFile()) return [root];
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.isFile()) out.push(path);
    }
  };
  walk(root);
  return out.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

/** sha256 over a file or a directory tree: each file as relative path + NUL + bytes + NUL. */
export function hashTree(root: string): string {
  const hash = createHash("sha256");
  for (const path of files(root)) {
    hash.update(relative(root, path).split(sep).join("/"));
    hash.update("\0");
    hash.update(readFileSync(path));
    hash.update("\0");
  }
  return hash.digest("hex");
}

/** job.json of a job (§7.4: a Job plus pid and argv), wherever JobManager keeps it under jobs/. */
export function readJobRecord(id: string): { id: string; argv: string[]; pid: number } {
  if (!existsSync(JOBS_DIR)) throw new Error(`no ${JOBS_DIR}`);
  for (const path of files(JOBS_DIR).filter((p) => p.endsWith(".json"))) {
    try {
      const doc = JSON.parse(readFileSync(path, "utf8")) as { id?: unknown; argv?: unknown; pid?: unknown };
      if (doc.id === id && Array.isArray(doc.argv)) return doc as { id: string; argv: string[]; pid: number };
    } catch {
      // progress.jsonl neighbours or partial writes: not the record
    }
  }
  throw new Error(`no job.json with id ${id} under ${JOBS_DIR}`);
}
