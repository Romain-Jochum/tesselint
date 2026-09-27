// Epic health (DESIGN §2 Overview) and axis groups (§2 Matrix "epic boundaries").
import { describe, expect, it } from "vitest";
import { epicHealth, sortHealth } from "./health.ts";
import { axisGroups } from "./matrix.ts";
import type { Case, MatrixPacked, Proposal, StoryRow } from "./types.ts";

const story = (id: string, epic: string, order: number): StoryRow => ({
  id, epic, title: id, tag: "", words: 10, text_hash: "", text: id, atomic: null, order,
});
const STORIES = [story("A", "E1", 0), story("B", "E1", 1), story("C", "E2", 2), story("D", "E3", 3)];
// Pairs AB AC AD BC BD CD
const MATRIX: MatrixPacked = { ids: ["A", "B", "C", "D"], o: [0.9, 0.8, 0.1, 0.75, 0.2, 0.3], m: [0, 0, 0, 0, 0, 0], rel: [null, null, null, null, null, null], rel_order: [] };
const kase = (key: string, kind: Case["kind"], epics: string[]): Case => ({ key, kind, anchor: "", proposals: [key], strength: 1, borderline: false, epics, conflicts: [], order: 0 });
const merge = { key: "merge:A>B", kind: "merge", stories: ["A", "B"] } as unknown as Proposal;
const split = { key: "split:C", kind: "split", stories: ["C"] } as unknown as Proposal;

describe("epicHealth", () => {
  const rows = epicHealth(
    { epics: [{ id: "E1", name: "One" }, { id: "E2", name: "Two" }, { id: "E3", name: "Three" }], stories: STORIES },
    [merge, split],
    [kase("mg:B", "merge", ["E1"]), kase("split:C", "split", ["E2"])],
    MATRIX,
    0.7,
    (c) => c.kind === "split",
  );

  it("counts stories, cases, within-epic pairs, the top partner and the share decided", () => {
    expect(rows[0]).toMatchObject({ epic: "E1", live: 2, inProposals: 2, merges: 1, splits: 0, within: 1, partner: "E2", partnerPairs: 2, share: 0 });
    expect(rows[1]).toMatchObject({ epic: "E2", live: 1, inProposals: 1, splits: 1, within: 0, partner: "E1", partnerPairs: 2, share: 1 });
    expect(rows[2]).toMatchObject({ epic: "E3", partner: null, partnerPairs: 0, cases: 0, share: null });
  });

  it("sorts by a column with nulls last and document order as the tie-break", () => {
    expect(sortHealth(rows, "share", "desc").map((r) => r.epic)).toEqual(["E2", "E1", "E3"]);
    expect(sortHealth(rows, "share", "asc").map((r) => r.epic)).toEqual(["E1", "E2", "E3"]);
    expect(sortHealth(rows, "epic", "desc").map((r) => r.epic)).toEqual(["E3", "E2", "E1"]);
  });
});

describe("axisGroups", () => {
  it("groups consecutive labels by key", () => {
    const epic = new Map(STORIES.map((s) => [s.id, s.epic]));
    expect(axisGroups(["A", "B", "C", "D"], (id) => epic.get(id) ?? null)).toEqual([
      { key: "E1", start: 0, count: 2 },
      { key: "E2", start: 2, count: 1 },
      { key: "E3", start: 3, count: 1 },
    ]);
    expect(axisGroups(["A", "C", "B"], (id) => epic.get(id) ?? null).map((g) => g.start)).toEqual([0, 1, 2]);
  });
});
