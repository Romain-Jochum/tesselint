// Build stamp (docs/ui/DESIGN.md §5): writes <outDir>/build.json = {"source_hash": <sha256 hex>}.
//
// The hash covers web/src/** (except *.test.ts and *.test.tsx, so test edits never stale the
// committed assets), web/index.html, web/package-lock.json and
// web/vite.config.ts. Paths
// are POSIX and relative to the repo root (e.g. "web/src/main.tsx"), sorted by code point (UTF-8
// byte order, as Python's sorted() does), and each file is fed as path + "\0" + bytes + "\0".
// pytest recomputes the same hash and fails when the committed assets are stale.
//
// Usage: node scripts/stamp.mjs [outDir]   (outDir relative to web/, default the served static dir)

import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = resolve(WEB, "..");
const OUT = resolve(WEB, process.argv[2] ?? "../src/tesselint/server/static");

/** Every regular file under dir, recursively (symlinks and other entries are skipped). */
function walk(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(path));
    else if (entry.isFile() && !/\.test\.tsx?$/.test(entry.name)) files.push(path);
  }
  return files;
}

const toPosix = (abs) => relative(ROOT, abs).split(sep).join("/");
const byCodePoint = (a, b) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));

const inputs = [
  ...walk(join(WEB, "src")),
  join(WEB, "index.html"),
  join(WEB, "package-lock.json"),
  join(WEB, "vite.config.ts"),
];
const entries = inputs.map((abs) => ({ abs, path: toPosix(abs) })).sort((a, b) => byCodePoint(a.path, b.path));

const hash = createHash("sha256");
for (const { abs, path } of entries) {
  hash.update(path, "utf8");
  hash.update("\0");
  hash.update(readFileSync(abs));
  hash.update("\0");
}
const sourceHash = hash.digest("hex");

writeFileSync(join(OUT, "build.json"), JSON.stringify({ source_hash: sourceHash }) + "\n");
console.log(`stamp: ${entries.length} files, source_hash ${sourceHash} -> ${toPosix(join(OUT, "build.json"))}`);
