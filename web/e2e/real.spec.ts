// Real-data check, item 5 (docs/ui/DESIGN.md §6): `serve` on the real runs plus this spec, opt-in
// only. Run `uv run tesselint serve --no-open` (default port 8765) on the runs that hold the real
// run, then `TESSELINT_E2E_REAL=1 TESSELINT_E2E_REAL_STORIES=<n> npx playwright test --grep @real`
// (TESSELINT_E2E_REAL_URL overrides the server URL). It never touches the fixture state dir.
import { expect, test } from "@playwright/test";
import { TID } from "../src/lib/testids.ts";
import type { ProposalsResponse, RunsResponse } from "../src/lib/types.ts";
import { apiGet, collectBodies, measureStoryPaint, num, readBase } from "./helpers/app.ts";

const REAL = process.env["TESSELINT_E2E_REAL"] === "1";
// The real run's live story count: set it for your backlog (176 is the calibration backlog's).
const STORIES = Number(process.env["TESSELINT_E2E_REAL_STORIES"] ?? "176");

test.use({ baseURL: process.env["TESSELINT_E2E_REAL_URL"] ?? "http://127.0.0.1:8765" });

test("real run: the real story count, the derived case count, matrix paint ≤ 500 ms, no sk-or-", { tag: "@real" }, async ({ page, request }) => {
  test.skip(!REAL, "opt-in: set TESSELINT_E2E_REAL=1 and serve the real runs");
  test.setTimeout(120_000);

  const { latest, runs } = await apiGet<RunsResponse>(request, "/api/runs");
  expect(latest).toBeTruthy();
  const summary = runs.find((r) => r.ref === latest);
  expect(summary?.kind).toBe("real");
  expect(summary?.n_stories).toBe(STORIES);
  const ref = encodeURIComponent(latest!);
  const { cases } = await apiGet<ProposalsResponse>(request, `/api/runs/${ref}/proposals`);

  const bodies = await collectBodies(page, async () => {
    await page.goto(`/r/${ref}/overview`);
    await expect(page.getByTestId(TID.runKind)).toHaveText("real");
    await expect(page.getByTestId(TID.tile("stories"))).toContainText(num(STORIES));

    await page.goto(`/r/${ref}/review?status=all`);
    await expect(page.getByTestId(TID.caseRow)).toHaveCount(cases.length);

    // Load the story matrix once (data cached), go back to epics, then time the story-level paint.
    await page.goto(`/r/${ref}/matrix?level=stories`);
    await expect.poll(async () => (await readBase(page)).width).toBe(STORIES);
    await page.getByTestId(TID.level("epics")).click();
    await expect.poll(async () => (await readBase(page)).width).not.toBe(STORIES);
    const paintMs = await measureStoryPaint(page, STORIES);
    expect(paintMs, "§5 budget: matrix paint ≤ 500 ms").toBeLessThanOrEqual(500);
  });

  const leaks = bodies.filter((b) => b.includes("sk-or-")).map((b) => b.slice(0, 160));
  expect(leaks).toEqual([]);
});
