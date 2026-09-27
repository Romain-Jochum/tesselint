// E1 Overview (docs/ui/DESIGN.md §6 E1; §2 Overview tiles, Trust banner, Chrome run badge; §5 budget
// "open ≤ 2 s"; §2 Routes "`/` opens the latest run").
import { TID } from "../src/lib/testids.ts";
import { escapeRe, num } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun } from "./helpers/manifest.ts";

test("E1 Overview: opens in ≤ 2 s, the banner says not decisions, the tiles equal the manifest, the run shows synthetic", async ({
  page,
  manifest,
}) => {
  const ref = manifest.latest;
  const run = latestRun(manifest);
  const seed = manifest.seed_decisions.on_latest;

  await page.goto(`/r/${ref}/overview`);
  await expect(page.getByTestId(TID.tile("stories"))).toBeVisible();
  const openMs = await page.evaluate(() => performance.now());
  expect(openMs, "§5 budget: open ≤ 2 s").toBeLessThanOrEqual(2000);

  await expect(page.getByTestId(TID.trustBanner)).toContainText("not decisions");
  await expect(page.getByTestId(TID.tile("stories"))).toContainText(num(run.n_stories));
  await expect(page.getByTestId(TID.tile("merges"))).toContainText(`${run.cases.merge_groups} groups · ${run.proposals.merge} merges`);
  await expect(page.getByTestId(TID.tile("overlaps"))).toContainText(num(run.proposals.overlap));
  await expect(page.getByTestId(TID.tile("splits"))).toContainText(num(run.proposals.split));
  await expect(page.getByTestId(TID.tile("decided"))).toContainText(new RegExp(`\\d+/${run.cases.total} decided · ${seed.stale} stale`));
  await expect(page.getByTestId(TID.tile("cost"))).toContainText("US$");

  expect(run.kind).toBe("synthetic");
  await expect(page.getByTestId(TID.runKind)).toHaveText(run.kind);
  await expect(page.getByTestId(TID.runSwitcher)).toContainText(ref);

  // `/` opens the latest run.
  await page.goto("/");
  await expect(page).toHaveURL(new RegExp(`/r/${escapeRe(ref)}/overview$`));
  await expect(page.getByTestId(TID.tab("review"))).toBeVisible();
});
