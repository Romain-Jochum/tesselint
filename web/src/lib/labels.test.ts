// Readable labels (DESIGN §2 Review rows and conflict strip; §3 keys): no raw key reaches the reader.
import { describe, expect, it } from "vitest";
import { caseKeyOf, conflictPhrase, conflictText, keyLabel, mergeRelationWords, parseKey, relationLabel, topProbs } from "./labels.ts";

const TITLES: Record<string, string> = { "US-6.2": "Run a work party", "US-6.3": "Sign up for a work party" };
const titleOf = (id: string) => TITLES[id];

describe("parseKey and caseKeyOf", () => {
  it("reads case and proposal keys", () => {
    expect(parseKey("mg:US-6.3")).toEqual({ kind: "merge", ids: ["US-6.3"] });
    expect(parseKey("merge:US-6.2>US-6.3")).toEqual({ kind: "merge", ids: ["US-6.2", "US-6.3"] });
    expect(parseKey("split:US-6.2")).toEqual({ kind: "split", ids: ["US-6.2"] });
    expect(parseKey("overlap:US-1.2+US-4.3")).toEqual({ kind: "overlap", ids: ["US-1.2", "US-4.3"] });
    expect(parseKey("US-1.1")).toBeNull();
    expect(caseKeyOf("merge:US-6.2>US-6.3")).toBe("mg:US-6.3");
    expect(caseKeyOf("split:US-6.2")).toBe("split:US-6.2");
  });
});

describe("keyLabel", () => {
  it("names the kind and the story, with its title when known", () => {
    expect(keyLabel("mg:US-6.3", titleOf).text).toBe("Merge · US-6.3 “Sign up for a work party”");
    expect(keyLabel("split:US-6.2", titleOf)).toMatchObject({ kind: "Split", ids: "US-6.2", title: "Run a work party" });
    expect(keyLabel("merge:US-6.2>US-6.3", titleOf).ids).toBe("US-6.3 into US-6.2");
    expect(keyLabel("overlap:US-1.2+US-4.3", titleOf).text).toBe("Overlap · US-1.2, US-4.3");
    expect(keyLabel("mg:US-9.9", titleOf).text).toBe("Merge · US-9.9");
  });
});

describe("conflict sentences", () => {
  it("says what the conflict is, in words", () => {
    expect(conflictText(["split:US-6.2"], titleOf)).toBe("Conflicts with the split proposal on US-6.2 “Run a work party”");
    expect(conflictPhrase("mg:US-6.3", titleOf)).toBe("the merge group for US-6.3 “Sign up for a work party”");
    expect(conflictPhrase("merge:US-4.1>US-3.1", titleOf)).toBe("the merge of US-3.1 into US-4.1");
    expect(conflictText(["mg:US-6.3", "mg:US-4.2", "split:US-1.1"], titleOf)).toBe(
      "Conflicts with the merge groups for US-6.3, US-4.2 and the split proposal on US-1.1",
    );
  });
});

describe("relations in words", () => {
  it("names containment by story and orders the top probabilities", () => {
    expect(relationLabel("A_contains_B", "keep", "drop")).toBe("keep contains drop");
    expect(relationLabel("B_contains_A", "keep", "drop")).toBe("drop contains keep");
    expect(mergeRelationWords({ relation: "containment", keep: "US-6.2", drop: "US-6.3" })).toBe("US-6.2 contains US-6.3");
    expect(mergeRelationWords({ relation: "duplicate", keep: "US-7.1", drop: "US-8.1" })).toBe("US-8.1 duplicates US-7.1");
    expect(topProbs({ duplicate: 0.1, A_contains_B: 0.7, partial_overlap: 0.2 })).toEqual([
      ["A_contains_B", 0.7],
      ["partial_overlap", 0.2],
    ]);
    expect(topProbs(null)).toEqual([]);
  });
});
