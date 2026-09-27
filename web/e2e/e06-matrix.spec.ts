// E6 Matrix (docs/ui/DESIGN.md §6 E6; §2 Matrix: epic tooltip "E1 × E20 · 14 of 297 pairs ≥ 0.70 ·
// …", a click drills into Ei × Ej, story-level hover IDs, order document/cluster, `t` slider, pair
// drawer; §5 Heatmap: one ImageData at 1 px per cell, o < t at 25 % alpha; budget hover ≤ 50 ms).
import { ATTR, TID } from "../src/lib/testids.ts";
import type { RunBundle } from "../src/lib/types.ts";
import { apiGet, cellCenter, measureHover, param, px, readBase, type Image } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { latestRun } from "./helpers/manifest.ts";

const same = (a: Image, b: Image) => a.width === b.width && a.data.every((v, i) => v === b.data[i]);
const FADED = Math.round(0.25 * 255);

test("E6 Matrix: hover IDs in ≤ 50 ms, a click drills in, cluster order moves a known pixel, the slider fades cells, the drawer marks both sides", async ({
  page,
  request,
  manifest,
}) => {
  const ref = manifest.latest;
  const run = latestRun(manifest);
  const bundle = await apiGet<RunBundle>(request, `/api/runs/${ref}`);
  const ids = bundle.stories.map((s) => s.id);
  const epics = bundle.epics.map((e) => e.id);
  const n = ids.length;
  const heatmap = page.getByTestId(TID.heatmap);

  // Epic level (the default): the tooltip's figures, then a click drills into Ei × Ej.
  const cell = run.epic_cell;
  await page.goto(`/r/${ref}/matrix`);
  await expect(heatmap).toHaveAttribute(ATTR.rows, epics.join(","));
  const epicPoint = await cellCenter(page, epics.length, epics.length, epics.indexOf(cell.row), epics.indexOf(cell.col));
  await page.mouse.move(epicPoint.x, epicPoint.y);
  await expect(page.getByTestId(TID.heatmapTooltip)).toContainText(
    `${cell.row} × ${cell.col} · ${cell.pairs_at_or_above_t} of ${cell.pairs} pairs ≥ ${cell.t.toFixed(2)}`,
  );
  await page.mouse.click(epicPoint.x, epicPoint.y);
  await expect.poll(() => [param(page, "level"), param(page, "rows"), param(page, "cols")]).toEqual(["stories", cell.row, cell.col]);

  // Story level in document order: hover shows the pair's IDs within the budget.
  await page.goto(`/r/${ref}/matrix?level=stories&order=doc`);
  await expect(heatmap).toHaveAttribute(ATTR.rows, ids.join(","));
  const [i, j] = [ids.indexOf(run.pair.a), ids.indexOf(run.pair.b)];
  expect(i >= 0 && j >= 0, "the manifest pair is in the run").toBe(true);
  const pairPoint = await cellCenter(page, n, n, i, j);
  const hoverMs = await measureHover(page, pairPoint, [run.pair.a, run.pair.b]);
  expect(hoverMs, "§5 budget: hover ≤ 50 ms").toBeLessThanOrEqual(50);

  // Cluster order: the top cluster comes first, and display cell (0, 1) shows its first two members.
  const docImage = await readBase(page);
  expect([docImage.width, docImage.height]).toEqual([n, n]);
  await page.getByTestId(TID.order("cluster")).click();
  await expect.poll(() => param(page, "order")).toBe("cluster");
  await expect.poll(async () => same(await readBase(page), docImage)).toBe(false);
  const rows = ((await heatmap.getAttribute(ATTR.rows)) ?? "").split(",");
  const top = [...bundle.clusters].sort((a, b) => b.mean_overlap - a.mean_overlap)[0];
  expect(top, "the fixture has clusters").toBeDefined();
  expect(new Set(rows.slice(0, top!.members.length))).toEqual(new Set(top!.members));
  const clusterImage = await readBase(page);
  expect(px(clusterImage, 0, 1)).toEqual(px(docImage, ids.indexOf(rows[0]!), ids.indexOf(rows[1]!)));

  // The t slider fades cells below it: at t = 1 the pair's cell drops to 25 % alpha.
  await page.goto(`/r/${ref}/matrix?level=stories&order=doc`);
  await expect(heatmap).toHaveAttribute(ATTR.rows, ids.join(","));
  const t0 = bundle.thresholds["cluster"] ?? cell.t;
  const alphaBefore = px(await readBase(page), i, j)[3]!;
  if (run.pair.overlap >= t0) expect(alphaBefore).toBe(255);
  else expect(Math.abs(alphaBefore - FADED)).toBeLessThanOrEqual(1);
  expect(run.pair.overlap).toBeLessThan(1);
  await page.getByTestId(TID.tSlider).focus();
  await page.keyboard.press("End");
  await expect.poll(() => Number(param(page, "t"))).toBe(1);
  await expect.poll(async () => Math.abs(px(await readBase(page), i, j)[3]! - FADED)).toBeLessThanOrEqual(1);

  // A click opens the pair drawer with a shared phrase marked on each side.
  await page.mouse.click(pairPoint.x, pairPoint.y);
  const drawer = page.getByTestId(TID.pairDrawer);
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText(run.pair.a);
  await expect(drawer).toContainText(run.pair.b);
  await expect(drawer.getByTestId(TID.sideA).locator("mark").first()).toBeVisible();
  await expect(drawer.getByTestId(TID.sideB).locator("mark").first()).toBeVisible();
  expect(new Set(param(page, "pair")?.split("~"))).toEqual(new Set([run.pair.a, run.pair.b]));
});
