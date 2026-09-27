// E8 Diff (docs/ui/DESIGN.md §6 E8; §2 Runs: "Ticking two opens /runs/diff?a=&b=: stories added,
// removed, edited (word diff) and renamed; proposals new, gone, changed; decisions to re-review";
// §4.3 diff_runs).
import { ATTR, TID, type DiffSection } from "../src/lib/testids.ts";
import { attrs, byKey, param } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { diffKey } from "./helpers/manifest.ts";

const SECTIONS: DiffSection[] = ["added", "removed", "edited", "renamed", "new", "gone", "changed", "decisions_to_review"];

test("E8 Diff: ticking the two runs opens a diff that equals the manifest", async ({ page, manifest }) => {
  const { diff } = manifest;

  await page.goto("/runs");
  // Tick the newer run first: the diff still reads older (a) → newer (b).
  await byKey(page, TID.runTick, diff.b).check();
  await byKey(page, TID.runTick, diff.a).check();
  await expect(page).toHaveURL(/\/runs\/diff\?/);
  expect([param(page, "a"), param(page, "b")]).toEqual([diff.a, diff.b]);

  for (const section of SECTIONS) {
    const list = page.getByTestId(TID.diffList(section));
    await expect(list, section).toBeAttached();
    const shown = await attrs(list.locator(`[${ATTR.key}]`), ATTR.key);
    expect(shown.sort(), section).toEqual(diff[section].map(diffKey).sort());
  }

  if (diff.edited.length > 0) {
    await expect(page.getByTestId(TID.diffList("edited")).getByTestId(TID.wordDiff).first()).toBeVisible();
  }
});
