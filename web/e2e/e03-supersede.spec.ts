// E3 Supersede (docs/ui/DESIGN.md §6 E3; §2 Review "`a` accept (the group's other options derive
// `superseded`, not stored)"; §2 Cases "options, strongest first"; §3 superseded_by computed per run).
import { ATTR, TID } from "../src/lib/testids.ts";
import { pressAndSave } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun } from "./helpers/manifest.ts";
import { readDecisionsFile, readSeed } from "./helpers/state.ts";

test("E3 Supersede: accepting option 1 supersedes option 2, which stays unstored", async ({ page, manifest }) => {
  const ref = manifest.latest;
  const run = latestRun(manifest);
  const options = run.multi_option_group_options;
  expect(options.length).toBeGreaterThanOrEqual(2);
  const [first, second] = options as [string, string];
  const seed = readSeed();
  const url = `/r/${ref}/review?case=${encodeURIComponent(run.multi_option_group)}&status=all`;

  await page.goto(url);
  for (const [i, key] of options.entries()) {
    await expect(page.getByTestId(TID.option(i + 1))).toHaveAttribute(ATTR.key, key);
  }
  await page.keyboard.press("1");
  await pressAndSave(page, "a");

  await page.goto(url);
  await expect(page.getByTestId(TID.option(1))).toHaveAttribute(ATTR.status, "accept");
  await expect(page.getByTestId(TID.option(2))).toHaveAttribute(ATTR.status, "superseded");
  await expect(page.getByTestId(TID.option(2))).toContainText("superseded");

  const after = readDecisionsFile();
  expect(after.decisions[first]?.action).toBe("accept");
  expect(after.decisions[second], "superseded is derived, never stored").toEqual(seed.decisions[second]);
});
