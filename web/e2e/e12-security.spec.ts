// E12 Security (docs/ui/DESIGN.md §6 E12; §4.1 LocalGuard: token meta, X-TesseLint-Token, Origin
// check, CSP, redaction of `sk-or-…`; §1 boundary). The server runs with OPENROUTER_API_KEY=sk-or-canary.
import { TID } from "../src/lib/testids.ts";
import { andWaitForPost, collectBodies } from "./helpers/app.ts";
import { expect, test } from "./helpers/fixtures.ts";
import { TOKEN_HEADER } from "./helpers/paths.ts";

test("E12 Security: no sk-or- anywhere; a POST without the token or from a foreign Origin gets 403", async ({
  page,
  request,
  baseURL,
  manifest,
}) => {
  const ref = encodeURIComponent(manifest.latest);

  // Pages, their API calls, and an estimate (the only path that spawns the CLI with the key).
  const bodies = await collectBodies(page, async () => {
    for (const path of [`/r/${ref}/overview`, `/r/${ref}/review`, `/r/${ref}/matrix?level=stories`, "/runs"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
    }
    await page.goto("/runs/new?source=0&tier=t3");
    await andWaitForPost(page, "/api/estimates", () => page.getByTestId(TID.estimateButton).click());
  });
  for (const path of ["/", "/api/health", "/api/key", "/api/runs?include=dry", `/api/runs/${ref}`, `/api/decisions?run=${ref}`, "/api/jobs"]) {
    const res = await request.get(path);
    bodies.push(`${path}\n${JSON.stringify(res.headers())}\n${await res.text()}`);
  }
  const leaks = bodies.filter((b) => b.includes("sk-or-")).map((b) => b.slice(0, 160));
  expect(leaks).toEqual([]);

  // The page ships a real token and the CSP.
  const home = await request.get("/");
  expect(home.headers()["content-security-policy"]).toContain("default-src 'self'");
  const token = /<meta name="tesselint-token" content="([^"]*)"/.exec(await home.text())?.[1] ?? "";
  expect(token).not.toBe("");
  expect(token).not.toBe("__TESSELINT_TOKEN__");

  const body = { run: manifest.latest, changes: [] };
  const origin = new URL(baseURL ?? "http://127.0.0.1:8799").origin;

  const noToken = await request.post("/api/decisions", { data: body, headers: { Origin: origin } });
  expect(noToken.status()).toBe(403);
  expect(((await noToken.json()) as { error: { code: string } }).error.code).toBe("csrf");

  const foreign = await request.post("/api/decisions", { data: body, headers: { [TOKEN_HEADER]: token, Origin: "http://evil.example" } });
  expect(foreign.status()).toBe(403);

  const allowed = await request.post("/api/decisions", { data: body, headers: { [TOKEN_HEADER]: token, Origin: origin } });
  expect(allowed.status(), "the same request with the token and our Origin passes the guard").not.toBe(403);
});
