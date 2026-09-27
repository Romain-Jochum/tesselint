// E14 Source (docs/ui/DESIGN.md §6 E14; §1 "serve … never writes sources or run directories"; §2
// Overview "what changed vs the current source" and the `source edited` chip linking to Re-run).
import { ATTR, TID } from "../src/lib/testids.ts";
import { andWaitForPost, attrs, pressAndSave, waitForGet } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun, runDir } from "./helpers/manifest.ts";
import { SEED_PATH, SOURCE_PATH } from "./helpers/paths.ts";
import { hashTree } from "./helpers/state.ts";

test("E14 Source: source and fixture hashes are unchanged after deciding, undoing, re-deriving, estimating and exporting", async ({
  page,
  manifest,
}) => {
  test.setTimeout(90_000);
  const ref = manifest.latest;
  const watched = [SOURCE_PATH, SEED_PATH, runDir(manifest, manifest.previous), runDir(manifest, manifest.latest)];
  const before = watched.map(hashTree);

  // Decide, then undo.
  await page.goto(`/r/${ref}/review?case=${encodeURIComponent(latestRun(manifest).first_cases[0]!)}`);
  await expect(page.getByTestId(TID.casePane)).toBeVisible();
  await pressAndSave(page, "a");
  await pressAndSave(page, "u", "/api/decisions/undo");

  // Re-derive with other thresholds.
  const derived = waitForGet(page, `/api/runs/${ref}/proposals`, { merge: "0.5", cluster: "0.6", split: "0.7" });
  await page.goto(`/r/${ref}/review?status=all&th=m0.5,c0.6,s0.7`);
  expect((await derived).ok()).toBe(true);

  // Estimate (spawns the fake CLI) and write the exports.
  await page.goto("/runs/new?source=0&tier=t3");
  await andWaitForPost(page, "/api/estimates", () => page.getByTestId(TID.estimateButton).click());
  await page.goto(`/r/${ref}/export`);
  await andWaitForPost(page, "/api/exports", () => page.getByTestId(TID.writeExports).click());

  expect(watched.map(hashTree)).toEqual(before);

  // Overview lists the source's changes against the run, and the chip links to Re-run.
  await page.goto(`/r/${ref}/overview`);
  for (const change of ["edited", "added", "removed"] as const) {
    const list = page.getByTestId(TID.sourceChanges(change));
    await expect(list, change).toBeAttached();
    expect((await attrs(list.locator(`[${ATTR.key}]`), ATTR.key)).sort(), change).toEqual([...manifest.source_vs_latest[change]].sort());
  }
  await expect(page.getByTestId(TID.sourceEditedChip)).toHaveAttribute("href", /^\/runs\/new/);
});
