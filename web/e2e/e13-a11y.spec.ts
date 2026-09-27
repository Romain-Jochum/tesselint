// E13 Accessibility (docs/ui/DESIGN.md §6 E13; §5 Accessibility: WCAG 2.2 AA, 2 px focus ring; §2
// Review layout: "at 1440 × 900 both texts, evidence and conflicts fit without page scroll").
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { TID } from "../src/lib/testids.ts";
import type { Case, ProposalsResponse, RunBundle } from "../src/lib/types.ts";
import { apiGet } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";

async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  const html = page.locator("html");
  if ((await html.getAttribute("data-theme")) !== theme) await page.getByTestId(TID.themeToggle).click();
  await expect(html).toHaveAttribute("data-theme", theme);
}

test("E13 Accessibility: no serious axe issue in either theme, visible focus, the largest case fits 1440 × 900", async ({
  page,
  request,
  manifest,
}) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const ref = manifest.latest;
  const bundle = await apiGet<RunBundle>(request, `/api/runs/${ref}`);
  const { cases } = await apiGet<ProposalsResponse>(request, `/api/runs/${ref}/proposals`);

  // The largest case: the most words across the stories it shows.
  const words = new Map(bundle.stories.map((s) => [s.id, s.words]));
  const shown = (c: Case) => new Set([c.anchor, ...c.proposals.flatMap((k) => k.replace(/^[a-z]+:/, "").split(/[>+]/))]);
  const size = (c: Case) => [...shown(c)].reduce((sum, id) => sum + (words.get(id) ?? 0), 0);
  const largest = [...cases].sort((a, b) => size(b) - size(a))[0];
  expect(largest, "the run has cases").toBeDefined();
  const review = `/r/${ref}/review?case=${encodeURIComponent(largest!.key)}&status=all`;

  for (const theme of ["light", "dark"] as const) {
    for (const path of [`/r/${ref}/overview`, review, `/r/${ref}/matrix`]) {
      await page.goto(path);
      await expect(page.locator("main")).toBeVisible();
      await setTheme(page, theme);
      const { violations } = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
      const serious = violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id} (${v.nodes.length})`);
      expect(serious, `${theme} ${path}`).toEqual([]);
    }
  }

  // Visible focus: the first Tab stop draws a ≥ 2 px ring.
  await page.goto(`/r/${ref}/overview`);
  await expect(page.getByTestId(TID.tile("stories"))).toBeVisible();
  await page.keyboard.press("Tab");
  const ring = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const style = getComputedStyle(el);
    return { tag: el.tagName, outline: style.outlineStyle, width: parseFloat(style.outlineWidth), shadow: style.boxShadow };
  });
  expect(ring.tag).not.toBe("BODY");
  expect((ring.outline !== "none" && ring.width >= 2) || ring.shadow !== "none", JSON.stringify(ring)).toBe(true);

  // The largest case fits 1440 × 900 without page scroll, decision bar included.
  await page.goto(review);
  await expect(page.getByTestId(TID.casePane)).toBeVisible();
  const overflow = await page.evaluate(() => ({
    y: document.documentElement.scrollHeight - window.innerHeight,
    x: document.documentElement.scrollWidth - window.innerWidth,
  }));
  expect(overflow.y, "no vertical page scroll").toBeLessThanOrEqual(0);
  expect(overflow.x, "no horizontal page scroll").toBeLessThanOrEqual(0);
  for (const tid of [TID.casePane, TID.evidence, TID.decisionBar]) {
    const box = await page.getByTestId(tid).boundingBox();
    expect(box && box.y >= 0 && box.y + box.height <= 900, tid).toBe(true);
  }
});
