// E9 Re-run (docs/ui/DESIGN.md §6 E9; §2 Re-run steps 1–4 and exit-code messages; §4.4 JobManager:
// estimate first, then `[*cli, "run", …, "--max-usd", X, "--out", "<runs>/ui", "--progress-jsonl", P]`;
// §7.4 job.json = Job + pid + argv). The server runs the fake CLI named by TESSELINT_UI_CLI in
// playwright.config.ts, so nothing here can spend money. Its default mode must be `ok`.
import { rmSync } from "node:fs";
import { TID } from "../src/lib/testids.ts";
import type { Estimate, Health, Job } from "../src/lib/types.ts";
import { andWaitForPost, apiGet, byKey, escapeRe } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { loadManifest, runDir } from "./helpers/manifest.ts";
import { readJobRecord } from "./helpers/state.ts";

/** min(ceil_cent(1.3 × estimate), remaining, ui_max_usd), in integer cents (§2 Re-run step 3). */
function expectedCap(usd: number, remaining: number | null, uiMax: number): number {
  const cents = Math.ceil(Math.round(usd * 1.3 * 1e6) / 1e4);
  return Math.min(cents / 100, remaining ?? Infinity, uiMax);
}

let createdRun: string | null = null;

test.afterAll(() => {
  // The fake's run lands under <runs>/ui/ (§4.4 --out); keep the fixture tree as the builder wrote it.
  if (createdRun?.startsWith("ui~")) rmSync(runDir(loadManifest(), createdRun), { recursive: true, force: true });
});

test("E9 Re-run: Start stays disabled until ticked; the run ends at a listed run; the argv carries the estimate before --max-usd", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const health = await apiGet<Health>(request, "/api/health");

  await page.goto("/runs/new?source=0&tier=t3");
  const estimateResponse = await andWaitForPost(page, "/api/estimates", () => page.getByTestId(TID.estimateButton).click());
  expect(estimateResponse.status()).toBe(201);
  const estimate = (await estimateResponse.json()) as Estimate;
  await expect(page.getByTestId(TID.estimatePanel)).toBeVisible();
  await expect(page.getByTestId(TID.estimateCommand)).toContainText("--tier t3");

  // Confirm: the default cap, the tick box and a disabled Start.
  const cap = expectedCap(estimate.usd, estimate.key.remaining_usd, health.ui_max_usd);
  await expect.poll(async () => Number(await page.getByTestId(TID.capInput).inputValue())).toBeCloseTo(cap, 2);
  const tick = page.getByTestId(TID.spendTick);
  const start = page.getByTestId(TID.startButton);
  await expect(tick).not.toBeChecked();
  await expect(page.getByText(`I accept spending up to US$${cap.toFixed(2)}`)).toBeVisible();
  await expect(start).toBeDisabled();
  await tick.check();
  await expect(start).toBeEnabled();

  // Start: the job page follows the fake CLI to a finished run.
  const startResponse = await andWaitForPost(page, "/api/jobs", () => start.click());
  expect(startResponse.status()).toBe(202);
  const job = (await startResponse.json()) as Job;
  await expect(page).toHaveURL(new RegExp(`/runs/job/${escapeRe(job.id)}$`));
  await expect(page.getByTestId(TID.jobState)).toHaveText("done", { timeout: 90_000 });
  const finished = await apiGet<Job>(request, `/api/jobs/${encodeURIComponent(job.id)}`);
  expect(finished.exit_code).toBe(0);
  expect(finished.run_ref).toBeTruthy();
  createdRun = finished.run_ref;
  await expect(page.getByTestId(TID.jobRunLink)).toHaveAttribute("href", `/r/${finished.run_ref}/overview`);
  await page.goto("/runs");
  await expect(byKey(page, TID.runRow, finished.run_ref!)).toBeVisible();

  // The spawned argv: the estimate's run arguments, then the confirmed cap.
  const { argv } = readJobRecord(job.id);
  const run = argv.indexOf("run");
  const maxUsd = argv.indexOf("--max-usd");
  expect(run).toBeGreaterThanOrEqual(0);
  expect(maxUsd).toBeGreaterThan(run);
  expect(Number(argv[maxUsd + 1])).toBeCloseTo(cap, 2);
  expect(argv).not.toContain("--estimate-json");
  const estimated = argv.slice(run + 1, maxUsd);
  expect(estimated[estimated.indexOf("--tier") + 1]).toBe("t3");
  expect(estimated.some((a) => a.replace(/\\/g, "/").endsWith("tests/fixtures/ui/source.v1.json"))).toBe(true);
  expect(argv).toContain("--progress-jsonl");
  expect(argv[argv.indexOf("--out") + 1]?.replace(/\\/g, "/")).toMatch(/(^|\/)ui$/);
});

test("E9 Re-run: the job page shows the cap, auth and CLI error messages", async ({ page }) => {
  const finished = (id: string, exit_code: number, message: string | null): Job => ({
    id,
    state: "failed",
    exit_code,
    message,
    run_ref: null,
    max_usd: 0.05,
    spent_usd: 0.05,
    log_tail: ["…"],
    phases: [{ name: "fwd", done: 12, total: 40 }],
  });
  const table: [number, string | null, string][] = [
    [3, null, "Stopped at the cap; resume pays only missing answers"],
    [5, null, "Jev refused the key or credits"],
    [4, "error: US-3.1 has no title (line 12)", "error: US-3.1 has no title (line 12)"],
  ];
  for (const [code, message, text] of table) {
    const id = `e2e-exit-${code}`;
    await page.route(`**/api/jobs/${id}`, (route) => route.fulfill({ json: finished(id, code, message) }));
    await page.goto(`/runs/job/${id}`);
    await expect(page.getByTestId(TID.jobMessage), `exit ${code}`).toHaveText(text);
  }
});
