// Phrases contract: DESIGN §5 "Phrases" (tokens, longest shared runs of >= 3 tokens, stopword-only
// runs skipped, 4-gram fallback above 250k DP cells, twin run indexes, <mark> segments) and the
// word diff of §2 (stale cases, edited stories).
import { describe, expect, it } from "vitest";
import { sharedPhrases, tokenize, toSegments, wordDiff, type Span } from "./phrases.ts";

const slice = (text: string, s: Span) => text.slice(s.start, s.end);
const words = (text: string) => tokenize(text).map((t) => t.text);

describe("tokenize (§5 token pattern)", () => {
  it("keeps apostrophes and hyphens inside words, lower-cases, and records offsets", () => {
    expect(tokenize("The user's e-mail, Café 42")).toEqual([
      { text: "the", start: 0, end: 3 },
      { text: "user's", start: 4, end: 10 },
      { text: "e-mail", start: 11, end: 17 },
      { text: "café", start: 19, end: 23 },
      { text: "42", start: 24, end: 26 },
    ]);
  });

  it("splits on punctuation outside words, handles the curly apostrophe, ignores symbol-only text", () => {
    expect(words("US-1.1’s (draft) -- done!")).toEqual(["us-1", "1’s", "draft", "done"]);
    expect(tokenize(" -- … !? ")).toEqual([]);
  });
});

describe("sharedPhrases (§5)", () => {
  it("highlights the longest common run as one span per side", () => {
    const a = "As a manager I can export the monthly sales report to PDF";
    const b = "Export the monthly sales report as a spreadsheet";
    const { a: sa, b: sb } = sharedPhrases(a, b);
    expect(sa).toHaveLength(1);
    expect(sb).toHaveLength(1);
    expect(slice(a, sa[0]!)).toBe("export the monthly sales report");
    expect(slice(b, sb[0]!)).toBe("Export the monthly sales report");
    expect(sa[0]!.run).toBe(sb[0]!.run);
  });

  it("finds every run, gives twins the same run index and distinct runs distinct indexes", () => {
    const a = "Reset password by email link. The admin can view audit log entries";
    const b = "Admins view audit log entries daily and reset password by email link";
    const { a: sa, b: sb } = sharedPhrases(a, b);
    expect(sa).toHaveLength(2);
    expect(sb).toHaveLength(2);
    expect(new Set(sa.map((s) => s.run)).size).toBe(2);
    for (const span of sa) {
      const twin = sb.find((s) => s.run === span.run);
      expect(twin).toBeDefined();
      expect(slice(b, twin!).toLowerCase()).toBe(slice(a, span).toLowerCase());
    }
    expect(sa.map((s) => slice(a, s).toLowerCase()).sort()).toEqual([
      "reset password by email link",
      "view audit log entries",
    ]);
  });

  it("ignores runs shorter than 3 tokens and runs made only of stopwords", () => {
    expect(sharedPhrases("export sales report", "export sales figures")).toEqual({ a: [], b: [] });
    expect(sharedPhrases("cats and it is not dogs", "birds and it is not fish")).toEqual({ a: [], b: [] });
  });

  it("uses each token once: a phrase repeated on one side is matched once", () => {
    const a = "sync calendar events nightly and sync calendar events hourly";
    const b = "please sync calendar events";
    const { a: sa, b: sb } = sharedPhrases(a, b);
    expect(sa).toHaveLength(1);
    expect(sb).toHaveLength(1);
    expect(slice(a, sa[0]!)).toBe("sync calendar events");
  });

  it("returns spans sorted by start that never overlap", () => {
    const a = "view order history page and download order invoices as pdf files";
    const b = "download order invoices as pdf files from the view order history page";
    for (const spans of Object.values(sharedPhrases(a, b))) {
      for (let i = 1; i < spans.length; i++) {
        expect(spans[i]!.start).toBeGreaterThanOrEqual(spans[i - 1]!.end);
      }
    }
  });

  it("falls back to shared 4-grams above 250k DP cells and still finds a planted phrase", () => {
    const filler = (p: string) => Array.from({ length: 600 }, (_, i) => `${p}${i}`).join(" ");
    const a = `${filler("w")} alpha bravo charlie delta ${filler("v")}`;
    const b = `${filler("x")} alpha bravo charlie delta ${filler("y")}`;
    const started = performance.now();
    const { a: sa, b: sb } = sharedPhrases(a, b);
    expect(performance.now() - started).toBeLessThan(1000);
    expect(sa.map((s) => slice(a, s))).toContain("alpha bravo charlie delta");
    expect(sb.map((s) => slice(b, s))).toContain("alpha bravo charlie delta");
  });
});

describe("toSegments (§5 <mark> rendering)", () => {
  it("cuts text into plain and highlighted pieces that concatenate to the input", () => {
    const text = "export the monthly sales report to PDF";
    const segments = toSegments(text, [{ start: 7, end: 31, run: 3 }]);
    expect(segments).toEqual([
      { text: "export ", run: null },
      { text: "the monthly sales report", run: 3 },
      { text: " to PDF", run: null },
    ]);
    expect(segments.map((s) => s.text).join("")).toBe(text);
    expect(toSegments("no shared words", [])).toEqual([{ text: "no shared words", run: null }]);
  });
});

describe("wordDiff (§2 stale cases)", () => {
  const oldText = "The user can export reports";
  const newText = "The admin can export PDF reports";
  const join = (ops: string[]) =>
    wordDiff(oldText, newText)
      .filter((p) => ops.includes(p.op))
      .map((p) => p.text)
      .join(" ");

  it("rebuilds old from same + del and new from same + add, marking only the changed words", () => {
    expect(words(join(["same", "del"]))).toEqual(words(oldText));
    expect(words(join(["same", "add"]))).toEqual(words(newText));
    expect(words(join(["del"]))).toEqual(["user"]);
    expect(words(join(["add"]))).toEqual(["admin", "pdf"]);
    expect(wordDiff(newText, newText).every((p) => p.op === "same")).toBe(true);
  });
});
