// E2 Keyboard (docs/ui/DESIGN.md §6 E2; §2 Review keys and URL `case`/`opt`; "The next case opens
// after 150 ms"; §3 History: each POST is one batch, rev += 1, undo appends the inverse with undo_of).
import { ATTR, TID } from "../src/lib/testids.ts";
import { param, pressAndSave } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun } from "./helpers/manifest.ts";
import { readDecisionsFile, readSeed } from "./helpers/state.ts";

test("E2 Keyboard: j 2 a r d, a note, then u yield exactly the expected decisions.json and rev", async ({ page, manifest }) => {
  const ref = manifest.latest;
  const cases = latestRun(manifest).first_cases;
  expect(cases, "manifest first_cases holds the first 5 cases").toHaveLength(5);
  const [c0, c1, c2, c3, c4] = cases as [string, string, string, string, string];
  const seed = readSeed();
  const option = (n: number) => page.getByTestId(TID.option(n));
  const optionKey = async (n: number) => {
    const key = await option(n).getAttribute(ATTR.key);
    expect(key, `option ${n} carries its proposal key`).toBeTruthy();
    return key as string;
  };

  await page.goto(`/r/${ref}/review?case=${encodeURIComponent(c0)}`);
  await expect(page.getByTestId(TID.casePane)).toBeVisible();

  // j: next case.
  await page.keyboard.press("j");
  await expect.poll(() => param(page, "case")).toBe(c1);

  // 2: pick option 2 (when the case has one), a: accept it; the next case opens.
  await expect(option(1)).toBeVisible();
  const hasTwo = (await option(2).count()) > 0;
  await page.keyboard.press("2");
  if (hasTwo) await expect.poll(() => param(page, "opt")).toBe("2");
  const accepted = await optionKey(hasTwo ? 2 : 1);
  await pressAndSave(page, "a");
  await expect.poll(() => param(page, "case")).toBe(c2);

  // r: reject option 1 of the next case.
  await expect(option(1)).toBeVisible();
  const rejected = await optionKey(1);
  await pressAndSave(page, "r");
  await expect.poll(() => param(page, "case")).toBe(c3);

  // d: defer option 1 of the next case.
  await expect(option(1)).toBeVisible();
  const deferred = await optionKey(1);
  await pressAndSave(page, "d");
  await expect.poll(() => param(page, "case")).toBe(c4);

  // n, text, Ctrl+Enter: a note alone; then u undoes that batch.
  await page.keyboard.press("n");
  const note = page.getByTestId(TID.noteInput);
  await expect(note).toBeFocused();
  await note.fill("e2e: check the wording");
  await pressAndSave(page, "Control+Enter");
  await expect(note).toBeHidden();
  await pressAndSave(page, "u", "/api/decisions/undo");

  const after = readDecisionsFile();
  const seedHistory = seed.history ?? [];
  expect(after.rev).toBe(seed.rev + 5);
  expect(after.history).toHaveLength(seedHistory.length + 5);
  expect(after.history.at(-1)?.undo_of).toBe(seed.rev + 4);

  const expected: Record<string, string> = { [accepted]: "accept", [rejected]: "reject", [deferred]: "defer" };
  expect(Object.keys(expected)).toHaveLength(3);
  const keys = new Set([...Object.keys(seed.decisions), ...Object.keys(expected)]);
  expect(Object.keys(after.decisions).sort()).toEqual([...keys].sort());
  for (const [key, record] of Object.entries(after.decisions)) {
    if (key in expected) {
      expect(record.action, key).toBe(expected[key]);
      expect(record.note, key).toBe(seed.decisions[key]?.note ?? "");
      expect(record.run, key).toBe(ref);
    } else {
      expect(record, key).toEqual(seed.decisions[key]);
    }
  }
});
