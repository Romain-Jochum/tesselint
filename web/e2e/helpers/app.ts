// Page and API helpers shared by the specs. Selectors come only from src/lib/testids.ts.
import { readFileSync } from "node:fs";
import type { APIRequestContext, Locator, Page, Response } from "@playwright/test";
import { expect } from "@playwright/test";
import { ATTR, TID } from "../../src/lib/testids.ts";

/** The value of a URL search param of the current page. */
export const param = (page: Page, name: string): string | null => new URL(page.url()).searchParams.get(name);

/** A number standing alone in a text (so 24 does not match 124). */
export const num = (n: number): RegExp => new RegExp(`(^|[^0-9.])${String(n).replace(".", "\\.")}([^0-9]|$)`);

export const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `[data-testid=tid][data-key=key]`: a keyed row or control. */
export const byKey = (page: Page | Locator, tid: string, key: string): Locator =>
  page.locator(`[data-testid="${tid}"][${ATTR.key}="${key.replace(/"/g, '\\"')}"]`);

/** Values of one attribute over every element a locator matches, in DOM order. */
export async function attrs(locator: Locator, name: string): Promise<string[]> {
  return locator.evaluateAll((els, n) => els.map((el) => el.getAttribute(n) ?? ""), name);
}

/** GET a JSON endpoint and fail loudly on a non-2xx reply. */
export async function apiGet<T>(request: APIRequestContext, path: string): Promise<T> {
  const res = await request.get(path);
  expect(res.ok(), `GET ${path} -> ${res.status()}`).toBe(true);
  return (await res.json()) as T;
}

const pathOf = (res: Response): string => new URL(res.url()).pathname;

/** Press a key (or run an action) and wait for the POST it causes to succeed (§2: every decision is one POST). */
export async function andWaitForPost(page: Page, path: string, act: () => Promise<void>): Promise<Response> {
  const posted = page.waitForResponse((r) => pathOf(r) === path && r.request().method() === "POST");
  await act();
  const res = await posted;
  expect(res.ok(), `POST ${path} -> ${res.status()}`).toBe(true);
  return res;
}

export const pressAndSave = (page: Page, key: string, path = "/api/decisions"): Promise<Response> =>
  andWaitForPost(page, path, () => page.keyboard.press(key));

/** Wait for a GET whose path (and optionally search params) match. */
export function waitForGet(page: Page, path: string, params: Record<string, string> = {}): Promise<Response> {
  return page.waitForResponse((r) => {
    const url = new URL(r.url());
    return (
      url.pathname === path &&
      r.request().method() === "GET" &&
      Object.entries(params).every(([k, v]) => url.searchParams.get(k) === v)
    );
  });
}

/** Click a download control and return the saved file's path and text. */
export async function download(page: Page, tid: string): Promise<{ path: string; text: string }> {
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByTestId(tid).click()]);
  const path = await dl.path();
  return { path, text: readFileSync(path, "utf8") };
}

// ---- Heatmap (DESIGN §5: one ImageData at 1 px per cell, overlay canvas on top) ----

export interface Image {
  width: number;
  height: number;
  data: number[];
}

/** The base canvas pixels (TID.heatmapBase: 2d context, intrinsic size cols × rows). */
export async function readBase(page: Page): Promise<Image> {
  return page.getByTestId(TID.heatmapBase).evaluate((el) => {
    const canvas = el as HTMLCanvasElement;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("heatmap base canvas has no 2d context");
    return { width: canvas.width, height: canvas.height, data: Array.from(ctx.getImageData(0, 0, canvas.width, canvas.height).data) };
  });
}

/** RGBA of display cell (row, col). */
export function px(image: Image, row: number, col: number): number[] {
  const at = (row * image.width + col) * 4;
  return image.data.slice(at, at + 4);
}

/** Page coordinates of the centre of display cell (row, col) in a grid of `rows` × `cols` over the overlay. */
export async function cellCenter(page: Page, rows: number, cols: number, row: number, col: number): Promise<{ x: number; y: number }> {
  const box = await page.getByTestId(TID.heatmapOverlay).boundingBox();
  if (!box) throw new Error("heatmap overlay is not visible");
  return { x: box.x + ((col + 0.5) * box.width) / cols, y: box.y + ((row + 0.5) * box.height) / rows };
}

/**
 * Hover latency measured inside the page (§5 budget "hover ≤ 50 ms"): from the pointermove event's
 * timeStamp to the first DOM mutation after which the tooltip's text holds every expected ID.
 */
export async function measureHover(page: Page, point: { x: number; y: number }, ids: string[]): Promise<number> {
  await page.mouse.move(0, 0);
  await page.evaluate(
    ({ tid, expected }) => {
      const w = window as unknown as { __hover: { moved: number; shown: number } };
      w.__hover = { moved: 0, shown: 0 };
      window.addEventListener("pointermove", (e) => (w.__hover.moved = e.timeStamp), { capture: true });
      const check = () => {
        const tip = document.querySelector(`[data-testid="${tid}"]`);
        const text = tip?.textContent ?? "";
        if (!w.__hover.shown && w.__hover.moved && expected.every((id) => text.includes(id))) w.__hover.shown = performance.now();
      };
      new MutationObserver(check).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
    },
    { tid: TID.heatmapTooltip, expected: ids },
  );
  await page.mouse.move(point.x, point.y);
  await expect(page.getByTestId(TID.heatmapTooltip)).toContainText(ids[0] ?? "");
  const { moved, shown } = await page.evaluate(() => (window as unknown as { __hover: { moved: number; shown: number } }).__hover);
  expect(shown, "tooltip never showed every expected ID").toBeGreaterThan(0);
  return shown - moved;
}

/**
 * Matrix paint time measured inside the page (§5 budget "matrix paint ≤ 500 ms"): from clicking the
 * Stories level control to the base canvas being n px wide with cell (0, 1) painted.
 */
export async function measureStoryPaint(page: Page, n: number): Promise<number> {
  return page.evaluate(
    ({ levelTid, baseTid, size }) =>
      new Promise<number>((resolve, reject) => {
        const start = performance.now();
        (document.querySelector(`[data-testid="${levelTid}"]`) as HTMLElement | null)?.click();
        const tick = () => {
          const canvas = document.querySelector(`[data-testid="${baseTid}"]`) as HTMLCanvasElement | null;
          if (canvas && canvas.width === size) {
            const alpha = canvas.getContext("2d")?.getImageData(1, 0, 1, 1).data[3] ?? 0;
            if (alpha > 0) return resolve(performance.now() - start);
          }
          if (performance.now() - start > 10_000) return reject(new Error("the story matrix never painted"));
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }),
    { levelTid: TID.level("stories"), baseTid: TID.heatmapBase, size: n },
  );
}

/** Every response body (and the final DOM) seen while visiting paths, for the `sk-or-` scan (§4.1). */
export async function collectBodies(page: Page, visit: () => Promise<void>): Promise<string[]> {
  const bodies: string[] = [];
  const pending: Promise<void>[] = [];
  const onResponse = (res: Response) => {
    pending.push(
      res
        .text()
        .then((text) => void bodies.push(`${res.url()}\n${JSON.stringify(res.headers())}\n${text}`))
        .catch(() => undefined),
    );
  };
  page.on("response", onResponse);
  try {
    await visit();
  } finally {
    page.off("response", onResponse);
  }
  await Promise.all(pending);
  bodies.push(await page.content());
  return bodies;
}
