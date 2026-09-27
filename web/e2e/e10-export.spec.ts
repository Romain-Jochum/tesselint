// E10 Export (docs/ui/DESIGN.md §6 E10; §2 Export: accepted merges, then splits, then overlaps;
// stale items only with include_stale=1; download .md/.json or write to .tesselint/exports/; §7.4
// export JSON schema and Markdown).
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { basename, isAbsolute, resolve } from "node:path";
import { ATTR, TID } from "../src/lib/testids.ts";
import type { RunBundle } from "../src/lib/types.ts";
import { andWaitForPost, apiGet, download, pressAndSave } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun } from "./helpers/manifest.ts";
import { EXPORTS_DIR, EXPORT_SCHEMA, REPO } from "./helpers/paths.ts";
import { readSeed } from "./helpers/state.ts";

/** Validate with the project's own `jsonschema` (a §4.5 dependency); throws with the error text. */
function validateExport(path: string): void {
  const script = "import json,sys,jsonschema; jsonschema.validate(json.load(open(sys.argv[2])), json.load(open(sys.argv[1])))";
  try {
    execFileSync("uv", ["run", "--quiet", "python", "-c", script, EXPORT_SCHEMA, path], { cwd: REPO, stdio: "pipe" });
  } catch (error) {
    const stderr = (error as { stderr?: Buffer }).stderr?.toString() ?? String(error);
    throw new Error(`export JSON does not validate against ${EXPORT_SCHEMA}:\n${stderr.slice(-2000)}`);
  }
}

const RANK = { merge: 0, split: 1, untangle: 2 } as const;

test("E10 Export: the Markdown has IDs, titles and hashes; the JSON validates; the write creates both files", async ({
  page,
  request,
  manifest,
}) => {
  const ref = manifest.latest;
  const run = latestRun(manifest);
  const bundle = await apiGet<RunBundle>(request, `/api/runs/${ref}`);
  const stories = new Map(bundle.stories.map((s) => [s.id, s]));

  // Accept option 1 of the multi-option group, so the export has at least one fresh merge.
  const option1 = run.multi_option_group_options[0]!;
  await page.goto(`/r/${ref}/review?case=${encodeURIComponent(run.multi_option_group)}&status=all`);
  await expect(page.getByTestId(TID.option(1))).toHaveAttribute(ATTR.key, option1);
  await page.keyboard.press("1");
  await pressAndSave(page, "a");

  // Expected items: accepted, neither stale nor missing (stale only with include_stale=1).
  const seed = readSeed();
  const excluded = new Set([...manifest.seed_decisions.stale_keys, ...manifest.seed_decisions.missing_keys]);
  const expected = new Map<string, { id: string; title: string; hash: string }[]>();
  for (const [key, d] of Object.entries(seed.decisions)) {
    if (d.action === "accept" && !excluded.has(key)) {
      expected.set(key, d.stories.map((id) => ({ id, title: d.titles[id] ?? "", hash: d.text_hashes[id] ?? "" })));
    }
  }
  const [keep, drop] = option1.replace(/^merge:/, "").split(">") as [string, string];
  expected.set(
    option1,
    [keep, drop].map((id) => ({ id, title: stories.get(id)?.title ?? "", hash: stories.get(id)?.text_hash ?? "" })),
  );

  await page.goto(`/r/${ref}/export`);
  await expect(page.getByTestId(TID.exportPreview)).toContainText(keep);

  // Markdown: every expected story's ID, title and hash.
  const md = await download(page, TID.downloadMd);
  for (const [key, list] of expected) {
    for (const s of list) {
      expect(md.text, `${key} ${s.id}`).toContain(s.id);
      expect(md.text, `${key} ${s.id} title`).toContain(s.title);
      expect(md.text, `${key} ${s.id} hash`).toContain(s.hash.slice(0, 12));
    }
  }
  expect(md.text).toContain("Apply in order; if a story's text no longer matches, skip it and report its ID.");

  // JSON: schema-valid, in order, the expected keys and no stale ones.
  const json = await download(page, TID.downloadJson);
  validateExport(json.path);
  const doc = JSON.parse(json.text) as { run: { ref: string }; items: { order: number; key: string; action: keyof typeof RANK }[] };
  expect(doc.run.ref).toBe(ref);
  const keys = doc.items.map((i) => i.key);
  expect(keys).toEqual(expect.arrayContaining([...expected.keys()]));
  for (const key of manifest.seed_decisions.stale_keys) expect(keys).not.toContain(key);
  expect(doc.items.map((i) => i.order)).toEqual(doc.items.map((_, n) => n + 1));
  const ranks = doc.items.map((i) => RANK[i.action]);
  expect(ranks).toEqual([...ranks].sort((a, b) => a - b));

  // Write: both files under <state>/exports/.
  const written = await andWaitForPost(page, "/api/exports", () => page.getByTestId(TID.writeExports).click());
  expect(written.status()).toBe(201);
  const { md_path, json_path } = (await written.json()) as { md_path: string; json_path: string };
  for (const path of [md_path, json_path]) {
    const absolute = isAbsolute(path) ? path : resolve(REPO, path);
    expect(existsSync(absolute), absolute).toBe(true);
    expect(absolute.startsWith(EXPORTS_DIR)).toBe(true);
  }
  await expect(page.getByTestId(TID.exportWritten)).toContainText(basename(md_path));
});
