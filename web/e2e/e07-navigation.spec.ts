// E7 Navigation (docs/ui/DESIGN.md §6 E7; §2 Story: "neighbours ranked by o (top 20, o ≥ 0.4)"; §2
// Chrome: "Every story ID links to its detail", `/` search).
import { ATTR, TID } from "../src/lib/testids.ts";
import { attrs, escapeRe } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun } from "./helpers/manifest.ts";

test("E7 Navigation: neighbours are ≤ 2 interactions away and sorted by o; / then Enter opens a story", async ({ page, manifest }) => {
  const ref = manifest.latest;
  const run = latestRun(manifest);
  const { story, top5 } = run.neighbours_of;
  const neighbours = page.getByTestId(TID.neighbour);

  // `/` then Enter opens a story.
  await page.goto(`/r/${ref}/overview`);
  await expect(page.getByTestId(TID.tile("stories"))).toBeVisible();
  await page.keyboard.press("/");
  await expect(page.getByTestId(TID.searchInput)).toBeFocused();
  await page.keyboard.type(story);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/r/${escapeRe(ref)}/story/${escapeRe(story)}$`));

  // Neighbours: ranked by o, top 20, o ≥ 0.4; the manifest's top 5 first.
  await expect(neighbours.first()).toBeVisible();
  const keys = await attrs(neighbours, ATTR.key);
  const os = (await attrs(neighbours, ATTR.value)).map(Number);
  expect(keys.slice(0, top5.length)).toEqual(top5);
  expect(keys.length).toBeLessThanOrEqual(20);
  expect(os.every((o) => o >= 0.4)).toBe(true);
  expect(os).toEqual([...os].sort((a, b) => b - a));

  // From Review, one click on a story ID in the open case shows its neighbours.
  await page.goto(`/r/${ref}/review?case=${encodeURIComponent(run.first_cases[0]!)}`);
  const link = page.getByTestId(TID.casePane).locator(`a[href*="/r/${ref}/story/"]`).first();
  const target = (await link.getAttribute("href")) ?? "";
  await link.click();
  await expect(page).toHaveURL(new RegExp(`${escapeRe(target)}$`));
  await expect(neighbours.first()).toBeVisible();
});
