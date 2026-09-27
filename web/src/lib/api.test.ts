// API client and Re-run spend rules: DESIGN §4.1 (token meta, X-TesseLint-Token on non-GET, JSON
// body), §7.2 (errors {"error": {"code", "message"}}), §2 Re-run step 3 (default cap and Start
// rule) and the exit-code messages.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  EXIT_MESSAGES,
  TOKEN_HEADER,
  canStart,
  defaultCap,
  exitMessage,
  exportUrl,
  getRun,
  postDecisions,
  readToken,
} from "./api.ts";
import type { Job } from "./types.ts";

// `serve` injects the token before any script runs (§4.1), so set it before the module loads.
const TOKEN = vi.hoisted(() => {
  const token = "tok-e2e-0123456789";
  const meta = document.createElement("meta");
  meta.setAttribute("name", "tesselint-token");
  meta.setAttribute("content", token);
  document.head.appendChild(meta);
  return token;
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("token and requests (§4.1, §7.2)", () => {
  it("reads the token from the meta tag and POSTs JSON with it; the body carries only keys", async () => {
    expect(readToken(document)).toBe(TOKEN);
    expect(readToken(document.implementation.createHTMLDocument("x"))).toBe("");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(200, { rev: 2, decisions: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const body = { run: "mini~20260102-090000", changes: [{ key: "split:US-1.1", action: "accept" as const }] };
    await expect(postDecisions(body)).resolves.toEqual({ rev: 2, decisions: [] });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe("/api/decisions");
    expect(init?.method).toBe("POST");
    const headers = new Headers(init?.headers);
    expect(headers.get(TOKEN_HEADER)).toBe(TOKEN);
    expect(headers.get("Content-Type")).toMatch(/^application\/json/);
    expect(JSON.parse(String(init?.body))).toEqual(body);
  });

  it("turns an error body into ApiError with status and code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(404, { error: { code: "run_not_found", message: "no such run" } })));
    const failure = getRun("nope");
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({ status: 404, code: "run_not_found", message: "no such run" });
  });

  it("builds the export download URL (§7.2 /api/export?run&format&include_stale)", () => {
    const url = new URL(exportUrl("mini~20260102-090000", "json", true), "http://127.0.0.1:8765");
    expect(url.pathname).toBe("/api/export");
    expect(url.searchParams.get("run")).toBe("mini~20260102-090000");
    expect(url.searchParams.get("format")).toBe("json");
    expect(url.searchParams.get("include_stale")).toBe("1");
  });
});

describe("exitMessage (§2 Re-run exit codes)", () => {
  const job = (exit_code: number | null, message: string | null = null): Pick<Job, "exit_code" | "message"> => ({ exit_code, message });

  it("exits 3, 5, 6, 130 and 1 give the §2 messages", () => {
    const table: [number, string][] = [
      [3, "Stopped at the cap; resume pays only missing answers"],
      [5, "Jev refused the key or credits"],
      [6, "Too many failed calls"],
      [130, "Cancelled; partial outputs kept"],
      [1, "Unexpected; run it with --debug"],
    ];
    for (const [code, text] of table) {
      expect(EXIT_MESSAGES[code]).toBe(text);
      expect(exitMessage(job(code, "ignored")), `exit ${code}`).toBe(text);
    }
  });

  it("exit 4 shows the CLI's own error line; success and running jobs have no message", () => {
    expect(exitMessage(job(4, "error: US-3.1 has no title (line 12)"))).toBe("error: US-3.1 has no title (line 12)");
    expect(exitMessage(job(0))).toBeNull();
    expect(exitMessage(job(null))).toBeNull();
  });
});

describe("defaultCap: min(ceil_cent(1.3 × estimate), remaining, ui_max_usd) (§2 Re-run step 3)", () => {
  it("rounds 1.3 × estimate up to the cent", () => {
    expect(defaultCap(0.1, 1.12, 1)).toBe(0.13);
    expect(defaultCap(0.101, 1.12, 1)).toBe(0.14);
  });

  it("does not over-round on binary floating point (1.3 × 0.9 = 1.17, not 1.18)", () => {
    expect(defaultCap(0.9, 5, 5)).toBe(1.17);
  });

  it("is capped by the remaining balance and by ui_max_usd; an unknown balance is ignored", () => {
    expect(defaultCap(0.5, 0.4, 1)).toBe(0.4);
    expect(defaultCap(0.9, 1.12, 1)).toBe(1);
    expect(defaultCap(0.1, null, 1)).toBe(0.13); // unknown balance
  });
});

describe("canStart: ticked and estimate <= cap <= min(remaining, ui_max_usd) (§2, §4.4)", () => {
  const ok = { estimateUsd: 0.1, capUsd: 0.13, remainingUsd: 1.12, uiMaxUsd: 1, accepted: true };

  it("allows a ticked, valid cap, including both bounds", () => {
    expect(canStart(ok)).toBe(true);
    expect(canStart({ ...ok, capUsd: 0.1 })).toBe(true);
    expect(canStart({ ...ok, capUsd: 1 })).toBe(true);
  });

  it("refuses when not ticked, or the cap is below the estimate or above the balance or ui_max_usd", () => {
    const table: [string, Partial<typeof ok>][] = [
      ["not ticked", { accepted: false }],
      ["cap below the estimate", { capUsd: 0.09 }],
      ["cap above the balance", { remainingUsd: 0.12 }],
      ["cap above ui_max_usd", { uiMaxUsd: 0.12 }],
    ];
    for (const [label, change] of table) expect(canStart({ ...ok, ...change }), label).toBe(false);
  });
});
