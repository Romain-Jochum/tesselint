// Review cases and optimistic decisions: DESIGN §2 Review (URL filters kind, epic, min, bl, status,
// preset, sort; options strongest first; `a` supersedes siblings without storing them), §3 (computed
// status; orphaned = proposed false; a note alone keeps action null), §4.3 (cases), §5 ("Decisions
// are optimistic, roll back on error, and are replaced by the response").
import { describe, expect, it } from "vitest";
import {
  INITIAL_DECISIONS,
  caseOptions,
  caseStatus,
  decisionsReducer,
  effectiveDecisions,
  filterCases,
  matchesStatus,
  orphanedDecisions,
  sortCases,
  type CaseContext,
  type DecisionsState,
  type PendingBatch,
} from "./cases.ts";
import type { Action, Case, DecisionChange, DecisionView, DecisionsResponse, Proposal } from "./types.ts";
import type { ReviewSearch } from "./router.ts";

// ---- fixtures ----

function merge(keep: string, drop: string, m: number, o: number, borderline = false): Proposal {
  return {
    id: `M-${keep}-${drop}`,
    key: `merge:${keep}>${drop}`,
    kind: "merge",
    target: keep,
    stories: [keep, drop],
    decisive_value: m,
    borderline,
    conflicts: [],
    details: {
      relation: "containment",
      keep,
      drop,
      m,
      o,
      fwd: {},
      swap: null,
      cluster_keep: null,
      cluster_drop: null,
      cos: null,
      atomic_keep: null,
      atomic_drop: null,
      related: [],
      alternatives: [],
    },
  };
}

function overlap(members: string[], meanO: number): Proposal {
  return {
    id: `O-${members.join("-")}`,
    key: `overlap:${members.join("+")}`,
    kind: "overlap",
    target: null,
    stories: members,
    decisive_value: meanO,
    borderline: false,
    conflicts: [],
    details: { cluster: "C1", members, mean_o: meanO, edges: [], related: [] },
  };
}

function split(id: string, bundled: number): Proposal {
  return {
    id: `S-${id}`,
    key: `split:${id}`,
    kind: "split",
    target: id,
    stories: [id],
    decisive_value: bundled,
    borderline: true,
    conflicts: [],
    details: { story: id, bundled, words: 80, n_subs: 3, cluster: null, reaches: [], keeps: [] },
  };
}

const M1 = merge("US-1.1", "US-14.5", 0.92, 0.88);
const M2 = merge("US-3.2", "US-14.5", 0.71, 0.65);
const M3 = merge("US-2.1", "US-9.9", 0.8, 0.6, true);
const S1 = split("US-1.1", 0.85);
const OV1 = overlap(["US-4.1", "US-5.2"], 0.55);
const OV2 = overlap(["US-6.1", "US-7.1", "US-8.3"], 0.82);
const PROPOSALS = new Map([M1, M2, M3, S1, OV1, OV2].map((p) => [p.key, p]));

function kase(key: string, kind: Case["kind"], proposals: string[], strength: number, borderline: boolean, epics: string[], order: number): Case {
  return { key, kind, anchor: key, proposals, strength, borderline, epics, conflicts: [], order };
}

const MG_A = kase("mg:US-14.5", "merge", [M1.key, M2.key], 0.92, false, ["E1", "E3", "E14"], 3);
const MG_B = kase("mg:US-9.9", "merge", [M3.key], 0.8, true, ["E2", "E9"], 1);
const CS = kase(S1.key, "split", [S1.key], 0.85, true, ["E1"], 0);
const CO1 = kase(OV1.key, "overlap", [OV1.key], 0.55, false, ["E4", "E5"], 4);
const CO2 = kase(OV2.key, "overlap", [OV2.key], 0.82, false, ["E6", "E7", "E8"], 2);
const CASES = [CO1, MG_B, CS, MG_A, CO2]; // deliberately unsorted

function view(key: string, action: Action | null, status: DecisionView["status"] = "current", extra: Partial<DecisionView> = {}): DecisionView {
  return {
    key,
    kind: key.startsWith("merge:") ? "merge" : key.startsWith("split:") ? "split" : "overlap",
    target: null,
    stories: [],
    action,
    note: "",
    text_hashes: {},
    titles: {},
    run: "mini~20260101-090000",
    decided_at: "2026-01-01T09:00:00Z",
    status,
    changed: status === "stale" ? ["US-1.1"] : [],
    proposed: true,
    superseded_by: null,
    applied: null,
    ...extra,
  };
}

const byKey = (views: DecisionView[]) => new Map(views.map((v) => [v.key, v]));
const ctx = (views: DecisionView[] = []): CaseContext => ({ proposals: PROPOSALS, decisions: byKey(views) });
const keys = (cases: Case[]) => cases.map((c) => c.key);

// ---- status ----

describe("caseStatus (§2 status filter, §3 status)", () => {
  it("is undecided without decisions or with a note alone (action null)", () => {
    expect(caseStatus(CS, new Map())).toBe("undecided");
    expect(caseStatus(CS, byKey([view(S1.key, null, "current", { note: "ask PO" })]))).toBe("undecided");
  });

  it("a current accept, reject or defer decides a single-option case", () => {
    for (const action of ["accept", "reject", "defer"] as const) {
      expect(caseStatus(CS, byKey([view(S1.key, action)])), action).toBe("decided");
    }
  });

  it("a merge group is decided by one accepted option, or by every option rejected", () => {
    expect(caseStatus(MG_A, byKey([view(M2.key, "accept")]))).toBe("decided");
    expect(caseStatus(MG_A, byKey([view(M2.key, "reject")]))).toBe("undecided");
    expect(caseStatus(MG_A, byKey([view(M1.key, "reject"), view(M2.key, "reject")]))).toBe("decided");
  });

  it("a stale decision reopens the case as stale", () => {
    expect(caseStatus(MG_A, byKey([view(M1.key, "accept", "stale")]))).toBe("stale");
  });

  it("matchesStatus: open = undecided + stale; all = everything; orphaned matches no case", () => {
    const table = [
      ["undecided", "open", true],
      ["stale", "open", true],
      ["decided", "open", false],
      ["stale", "stale", true],
      ["undecided", "stale", false],
      ["decided", "decided", true],
      ["stale", "decided", false],
      ["undecided", "all", true],
      ["decided", "all", true],
      ["stale", "orphaned", false],
      ["decided", "orphaned", false],
    ] as const;
    for (const [status, filter, expected] of table) expect(matchesStatus(status, filter), `${status} in ${filter}`).toBe(expected);
  });
});

// ---- filters and sort ----

describe("filterCases (§2 Review URL)", () => {
  const all: ReviewSearch = { status: "all" };

  it("defaults to status=open sorted by strength: decided cases are hidden", () => {
    expect(keys(filterCases(CASES, {}, ctx([view(S1.key, "accept")])))).toEqual([MG_A.key, CO2.key, MG_B.key, CO1.key]);
  });

  it("status=stale keeps stale cases, status=open keeps them too", () => {
    const stale = ctx([view(M1.key, "accept", "stale"), view(S1.key, "reject")]);
    expect(keys(filterCases(CASES, { status: "stale" }, stale))).toEqual([MG_A.key]);
    expect(keys(filterCases(CASES, { status: "open" }, stale))).toContain(MG_A.key);
    expect(keys(filterCases(CASES, { status: "decided" }, stale))).toEqual([CS.key]);
  });

  it("status=all shows every case; status=orphaned shows no case", () => {
    expect(filterCases(CASES, all, ctx())).toHaveLength(5);
    expect(filterCases(CASES, { status: "orphaned" }, ctx())).toEqual([]);
  });

  it("kind keeps one kind, epic keeps cases touching that epic, min keeps strength >= min; filters combine", () => {
    expect(keys(filterCases(CASES, { ...all, kind: "split" }, ctx()))).toEqual([CS.key]);
    expect(keys(filterCases(CASES, { ...all, kind: "overlap" }, ctx()))).toEqual([CO2.key, CO1.key]);
    expect(keys(filterCases(CASES, { ...all, epic: "E1" }, ctx()))).toEqual([MG_A.key, CS.key]);
    expect(keys(filterCases(CASES, { ...all, min: 0.82 }, ctx()))).toEqual([MG_A.key, CS.key, CO2.key]);
    expect(keys(filterCases(CASES, { ...all, kind: "merge", min: 0.85 }, ctx()))).toEqual([MG_A.key]);
  });

  it("bl=only keeps borderline cases, bl=hide drops them, bl=all keeps both", () => {
    expect(keys(filterCases(CASES, { ...all, bl: "only" }, ctx()))).toEqual([CS.key, MG_B.key]);
    expect(keys(filterCases(CASES, { ...all, bl: "hide" }, ctx()))).toEqual([MG_A.key, CO2.key, CO1.key]);
    expect(filterCases(CASES, { ...all, bl: "all" }, ctx())).toHaveLength(5);
  });

  it("preset=o70 hides merges and overlaps below o 0.7 and keeps splits", () => {
    expect(keys(filterCases(CASES, { ...all, preset: "o70" }, ctx()))).toEqual([MG_A.key, CS.key, CO2.key]);
  });

  it("sort=doc orders by document order", () => {
    expect(keys(filterCases(CASES, { ...all, sort: "doc" }, ctx()))).toEqual([CS.key, MG_B.key, CO2.key, MG_A.key, CO1.key]);
  });
});

describe("sortCases, caseOptions, orphanedDecisions", () => {
  it("sorts by strength descending, ties in document order, without mutating the input", () => {
    const tie = kase("split:US-9.1", "split", ["split:US-9.1"], 0.92, false, [], 1);
    const input = [MG_A, tie, CO1];
    expect(keys(sortCases(input, "strength"))).toEqual([tie.key, MG_A.key, CO1.key]);
    expect(keys(input)).toEqual([MG_A.key, tie.key, CO1.key]);
  });

  it("gives a group's options in option order and skips keys the run no longer proposes", () => {
    expect(caseOptions(MG_A, PROPOSALS)).toEqual([M1, M2]);
    expect(caseOptions(MG_A, new Map([[M1.key, M1]]))).toEqual([M1]);
  });

  it("lists decisions whose proposal is gone (proposed false) as orphaned", () => {
    const gone = view("merge:US-40.1>US-40.2", "accept", "missing", { proposed: false });
    expect(orphanedDecisions([view(S1.key, "accept"), gone])).toEqual([gone]);
  });
});

// ---- optimistic decisions ----

const R0: DecisionsResponse = { rev: 7, decisions: [view(M1.key, "reject", "current", { note: "check wording" })] };
const loaded = (): DecisionsState => decisionsReducer(INITIAL_DECISIONS, { type: "loaded", response: R0 });
const acceptS1 = { id: 1, changes: [{ key: S1.key, action: "accept" as const }] };

describe("decisionsReducer (§5 optimistic, rollback, replace)", () => {
  it("loads server truth, then applies a submitted batch optimistically before the server answers", () => {
    expect(loaded().server).toEqual(R0);
    const state = decisionsReducer(loaded(), { type: "submit", batch: acceptS1 });
    expect(state.server).toEqual(R0);
    expect(state.pending).toEqual([acceptS1]);
    expect(effectiveDecisions(state)[S1.key]).toEqual({ action: "accept", note: "" });
  });

  it("replaces the overlay with the server response on save", () => {
    const r1: DecisionsResponse = { rev: 8, decisions: [...R0.decisions, view(S1.key, "defer")] };
    let state = decisionsReducer(loaded(), { type: "submit", batch: acceptS1 });
    state = decisionsReducer(state, { type: "saved", id: 1, response: r1 });
    expect(state.pending).toEqual([]);
    expect(state.server).toEqual(r1);
    expect(effectiveDecisions(state)[S1.key]).toEqual({ action: "defer", note: "" });
  });

  it("rolls back a failed batch, offers it for Retry, and clears it when retried", () => {
    const before = effectiveDecisions(loaded());
    let state = decisionsReducer(loaded(), { type: "submit", batch: acceptS1 });
    state = decisionsReducer(state, { type: "failed", id: 1 });
    expect(state.pending).toEqual([]);
    expect(state.failed).toEqual(acceptS1);
    expect(effectiveDecisions(state)).toEqual(before);
    state = decisionsReducer(state, { type: "submit", batch: { ...acceptS1, id: 2 } });
    expect(state.failed).toBeNull();
    expect(effectiveDecisions(state)[S1.key]?.action).toBe("accept");
  });

  it("keeps later pending batches overlaid when an earlier one is saved", () => {
    const deferS1 = { id: 2, changes: [{ key: S1.key, action: "defer" as const }] };
    let state = decisionsReducer(loaded(), { type: "submit", batch: acceptS1 });
    state = decisionsReducer(state, { type: "submit", batch: deferS1 });
    state = decisionsReducer(state, {
      type: "saved",
      id: 1,
      response: { rev: 8, decisions: [...R0.decisions, view(S1.key, "accept")] },
    });
    expect(state.pending).toEqual([deferS1]);
    expect(effectiveDecisions(state)[S1.key]?.action).toBe("defer");
  });

  it("keeps pending batches across a refetch", () => {
    let state = decisionsReducer(loaded(), { type: "submit", batch: acceptS1 });
    state = decisionsReducer(state, { type: "loaded", response: { rev: 9, decisions: R0.decisions } });
    expect(state.server?.rev).toBe(9);
    expect(effectiveDecisions(state)[S1.key]?.action).toBe("accept");
  });

  it("never mutates the previous state", () => {
    const frozen: DecisionsState = Object.freeze({ ...loaded(), pending: Object.freeze([]) as unknown as PendingBatch[] });
    expect(() => decisionsReducer(frozen, { type: "submit", batch: acceptS1 })).not.toThrow();
    expect(frozen.pending).toEqual([]);
  });
});

describe("effectiveDecisions (§2 keys, §3 records)", () => {
  const submit = (...changes: DecisionChange[]) =>
    effectiveDecisions(decisionsReducer(loaded(), { type: "submit", batch: { id: 9, changes } }));

  it("reads server decisions by key; an action keeps the existing note; clear empties the action and keeps the note", () => {
    expect(effectiveDecisions(loaded())).toEqual({ [M1.key]: { action: "reject", note: "check wording" } });
    expect(submit({ key: M1.key, action: "accept" })[M1.key]).toEqual({ action: "accept", note: "check wording" });
    expect(submit({ key: M1.key, action: "clear" })[M1.key]).toEqual({ action: null, note: "check wording" });
  });

  it("a note alone keeps action null; changes apply in order", () => {
    expect(submit({ key: S1.key, note: "split by role" })[S1.key]).toEqual({ action: null, note: "split by role" });
    expect(submit({ key: S1.key, action: "accept" }, { key: S1.key, action: "reject" })[S1.key]?.action).toBe("reject");
  });

  it("accepting one option stores nothing for its siblings: superseded is derived, not stored", () => {
    const result = effectiveDecisions(
      decisionsReducer(INITIAL_DECISIONS, { type: "submit", batch: { id: 1, changes: [{ key: M2.key, action: "accept" }] } }),
    );
    expect(Object.keys(result)).toEqual([M2.key]);
  });
});
