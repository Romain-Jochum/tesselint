// E11 Thresholds (docs/ui/DESIGN.md §6 E11; §2 Review URL "`th=m0.5,c0.7,s0.8` (re-derives in
// memory, never writes)"; §4.3 derive writes nothing).
import { TID } from "../src/lib/testids.ts";
import { num, waitForGet } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun, runDir } from "./helpers/manifest.ts";
import { hashTree } from "./helpers/state.ts";

test("E11 Thresholds: merge at 0.9 gives the manifest count and the run dir is unchanged", async ({ page, manifest }) => {
  const ref = manifest.latest;
  const run = latestRun(manifest);
  const dir = runDir(manifest, ref);
  const before = hashTree(dir);
  const merges = page.getByTestId(TID.kindCount("merge"));

  await page.goto(`/r/${ref}/review?status=all&kind=merge`);
  await expect(merges).toContainText(num(run.proposals.merge));

  const derived = waitForGet(page, `/api/runs/${ref}/proposals`, { merge: "0.9" });
  await page.goto(`/r/${ref}/review?status=all&kind=merge&th=m0.9`);
  expect((await derived).ok()).toBe(true);
  await expect(merges).toContainText(num(run.merges_at_merge_0_9));

  expect(hashTree(dir), "th re-derives in memory and never writes").toBe(before);
});
