// E5 Survival (docs/ui/DESIGN.md §6 E5; §3 computed status current/stale/missing, orphaned =
// proposed false, "never deleted"; §2 Review "Stale cases show a word diff", status filters).
import { ATTR, TID } from "../src/lib/testids.ts";
import type { DecisionsResponse } from "../src/lib/types.ts";
import { apiGet, attrs, byKey } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { caseKeyOf } from "./helpers/manifest.ts";
import { readSeed } from "./helpers/state.ts";

test("E5 Survival: run 1's decisions on run 2 match the manifest's counts, stale ones show a word diff, none are lost", async ({
  page,
  request,
  manifest,
}) => {
  const ref = manifest.latest;
  const expected = manifest.seed_decisions;
  const seed = readSeed();

  const { decisions } = await apiGet<DecisionsResponse>(request, `/api/decisions?run=${encodeURIComponent(ref)}`);
  const withStatus = (s: string) => decisions.filter((d) => d.status === s);
  expect(withStatus("current")).toHaveLength(expected.on_latest.current);
  expect(withStatus("stale")).toHaveLength(expected.on_latest.stale);
  expect(withStatus("missing")).toHaveLength(expected.on_latest.missing);
  expect(decisions.filter((d) => !d.proposed)).toHaveLength(expected.on_latest.orphaned);
  expect(withStatus("stale").map((d) => d.key).sort()).toEqual([...expected.stale_keys].sort());
  expect(withStatus("missing").map((d) => d.key).sort()).toEqual([...expected.missing_keys].sort());
  // None lost: every seed decision is still there.
  expect(decisions).toHaveLength(expected.total);
  expect(decisions.map((d) => d.key).sort()).toEqual(Object.keys(seed.decisions).sort());

  // Stale decisions reopen their cases under status=stale.
  const staleCases = [...new Set(withStatus("stale").filter((d) => d.proposed).map((d) => caseKeyOf(d.key)))].sort();
  expect(staleCases.length).toBeGreaterThan(0);
  await page.goto(`/r/${ref}/review?status=stale`);
  const rows = page.getByTestId(TID.caseRow);
  await expect(rows).toHaveCount(staleCases.length);
  expect((await attrs(rows, ATTR.key)).sort()).toEqual(staleCases);
  for (const key of staleCases) await expect(byKey(page, TID.caseRow, key)).toHaveAttribute(ATTR.status, "stale");

  // A stale case shows a word diff of stored against current text.
  await page.goto(`/r/${ref}/review?status=stale&case=${encodeURIComponent(staleCases[0]!)}`);
  const diff = page.getByTestId(TID.wordDiff).first();
  await expect(diff).toBeVisible();
  await expect(diff.locator("del, ins").first()).toBeVisible();

  // Missing decisions stay visible as orphaned.
  await page.goto(`/r/${ref}/review?status=orphaned`);
  const orphans = page.getByTestId(TID.orphanList);
  for (const key of expected.missing_keys) await expect(orphans.locator(`[${ATTR.key}="${key}"]`)).toBeVisible();

  await page.goto(`/r/${ref}/overview`);
  await expect(page.getByTestId(TID.tile("decided"))).toContainText(`${expected.on_latest.stale} stale`);
});
