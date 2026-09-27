// E4 Conflict (docs/ui/DESIGN.md §6 E4; §2 Merge pane "a conflict strip (US-1.1 is split S4 ·
// undecided · `c`)", key `c` jumps to the conflict; §2 Overview Hubs: stories kept by ≥ 3 merges).
import { TID } from "../src/lib/testids.ts";
import type { ProposalsResponse } from "../src/lib/types.ts";
import { apiGet, param } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun } from "./helpers/manifest.ts";

test("E4 Conflict: the hub's merge case shows its split, and c reaches it", async ({ page, request, manifest }) => {
  const ref = manifest.latest;
  const run = latestRun(manifest);
  const { proposals, cases, hubs } = await apiGet<ProposalsResponse>(request, `/api/runs/${ref}/proposals`);

  const keeps = proposals.filter((p) => p.kind === "merge" && p.details.keep === run.hub_story);
  expect(keeps.length, "the hub is kept by ≥ 3 merges").toBeGreaterThanOrEqual(3);
  expect(hubs.find((h) => h.story === run.hub_story)?.split).toBe(run.hub_split_key);
  const hubCase = cases.find((c) => c.kind === "merge" && c.conflicts.includes(run.hub_split_key));
  expect(hubCase, "a merge case conflicts with the hub's split").toBeDefined();

  await page.goto(`/r/${ref}/review?case=${encodeURIComponent(hubCase!.key)}&status=all`);
  const strip = page.getByTestId(TID.conflictStrip);
  await expect(strip).toContainText(run.hub_story);
  await expect(strip).toContainText(/split/i);

  await page.keyboard.press("c");
  await expect.poll(() => param(page, "case")).toBe(run.hub_split_key);
  await expect(page.getByTestId(TID.casePane)).toContainText(run.hub_story);
});
