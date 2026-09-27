// Review cases and decisions (docs/ui/DESIGN.md §2 Review: filters, sort, keys; §3 computed status;
// §4.3 cases; §5 "Decisions are optimistic, roll back on error, and are replaced by the response").

import type { Action, Case, DecisionChange, DecisionView, DecisionsResponse, N, Proposal, S } from "./types.ts";
import type { ReviewSearch, StatusFilter } from "./router.ts";

/** A case's review state (§2 status filter; §3 status): open = undecided + stale. */
export type CaseStatus = "undecided" | "stale" | "decided";

/** What filterCases needs besides the cases: proposals and decision views by key. */
export interface CaseContext {
  proposals: ReadonlyMap<S, Proposal>;
  decisions: ReadonlyMap<S, DecisionView>;
}

/** The preset=o70 cut (§2): merges and overlaps with o below it are hidden. */
export const PRESET_O70 = 0.7;

/** The status of a case from its proposals' decisions (§2, §3). */
export function caseStatus(c: Case, decisions: ReadonlyMap<S, DecisionView>): CaseStatus {
  const views = c.proposals.map((key) => decisions.get(key));
  if (views.some((v) => v?.status === "stale" && v.action !== null)) return "stale";
  const actions = views.map((v) => (v?.status === "current" ? v.action : null));
  // One accepted (or deferred) option settles the group; otherwise every option needs an answer.
  const settled = actions.some((a) => a === "accept" || a === "defer") || actions.every((a) => a !== null);
  return actions.length > 0 && settled ? "decided" : "undecided";
}

/** Whether a status passes the `status` filter (§2; `orphaned` matches no case). */
export function matchesStatus(status: CaseStatus, filter: StatusFilter): boolean {
  switch (filter) {
    case "open":
      return status !== "decided";
    case "all":
      return true;
    case "orphaned":
      return false;
    default:
      return status === filter;
  }
}

/** The o of a merge (details.o) or overlap (details.mean_o); null for a split. */
export function proposalO(p: Proposal): N | null {
  if (p.kind === "merge") return p.details.o;
  if (p.kind === "overlap") return p.details.mean_o;
  return null;
}

/** The preset=o70 test (§2): splits pass; merge and overlap cases need an option with o ≥ 0.7. */
function passesO70(c: Case, proposals: ReadonlyMap<S, Proposal>): boolean {
  if (c.kind === "split") return true;
  return caseOptions(c, proposals).some((p) => (proposalO(p) ?? -1) >= PRESET_O70);
}

/** Apply the Review URL filters (kind, epic, min, bl, status, preset) then `sort` (§2). */
export function filterCases(cases: readonly Case[], search: ReviewSearch, ctx: CaseContext): Case[] {
  const { kind, epic, min, bl = "all", status = "open", preset, sort = "strength" } = search;
  const kept = cases.filter(
    (c) =>
      (kind === undefined || c.kind === kind) &&
      (epic === undefined || c.epics.includes(epic)) &&
      (min === undefined || c.strength >= min) &&
      (bl === "all" || c.borderline === (bl === "only")) &&
      (preset !== "o70" || passesO70(c, ctx.proposals)) &&
      matchesStatus(caseStatus(c, ctx.decisions), status),
  );
  return sortCases(kept, sort);
}

/** Order cases by strength (descending) or document order (`order`) (§2 sort, §4.3). */
export function sortCases(cases: readonly Case[], sort: "strength" | "doc"): Case[] {
  const byDoc = (x: Case, y: Case) => x.order - y.order;
  const compare = sort === "doc" ? byDoc : (x: Case, y: Case) => y.strength - x.strength || byDoc(x, y);
  return [...cases].sort(compare);
}

/** A case's options (its proposals), strongest first, as keys 1–9 pick them (§2 Merge pane). */
export function caseOptions(c: Case, proposals: ReadonlyMap<S, Proposal>): Proposal[] {
  // The server lists options strongest first (§4.3); keys the run no longer proposes drop out.
  return c.proposals.flatMap((key) => proposals.get(key) ?? []);
}

/** Decisions whose proposal no longer exists (`proposed` false, §3): the `orphaned` filter. */
export function orphanedDecisions(decisions: readonly DecisionView[]): DecisionView[] {
  return decisions.filter((d) => !d.proposed);
}

// ---- Optimistic decisions (§5) ----

/** One POST worth of changes, applied optimistically until the server answers. */
export interface PendingBatch {
  id: N;
  changes: DecisionChange[];
}

/** Server truth plus the optimistic overlays; `failed` is the batch offered for Retry (§2). */
export interface DecisionsState {
  server: DecisionsResponse | null;
  pending: PendingBatch[];
  failed: PendingBatch | null;
}

export type DecisionsEvent =
  | { type: "loaded"; response: DecisionsResponse } // fetched or refetched
  | { type: "submit"; batch: PendingBatch } // optimistic apply
  | { type: "saved"; id: N; response: DecisionsResponse } // replaced by the response
  | { type: "failed"; id: N }; // rolled back, offered for Retry

export const INITIAL_DECISIONS: DecisionsState = { server: null, pending: [], failed: null };

/** Reduce a decisions event (§5): optimistic apply, replace by response, roll back on error. */
export function decisionsReducer(state: DecisionsState, event: DecisionsEvent): DecisionsState {
  switch (event.type) {
    case "loaded":
      return { ...state, server: event.response };
    case "submit":
      return { ...state, pending: [...state.pending, event.batch], failed: null };
    case "saved": {
      const pending = state.pending.filter((b) => b.id !== event.id);
      // A reply older than what we already hold (out-of-order saves) must not roll the view back.
      const older = state.server !== null && event.response.rev < state.server.rev;
      return { ...state, pending, server: older ? state.server : event.response };
    }
    case "failed": {
      const batch = state.pending.find((b) => b.id === event.id);
      if (!batch) return state;
      return { ...state, pending: state.pending.filter((b) => b !== batch), failed: batch };
    }
  }
}

/** Action and note per key as the UI shows them: server decisions with pending batches applied in order. */
export function effectiveDecisions(state: DecisionsState): Record<S, { action: Action | null; note: S }> {
  const result: Record<S, { action: Action | null; note: S }> = {};
  for (const d of state.server?.decisions ?? []) result[d.key] = { action: d.action, note: d.note };
  for (const change of state.pending.flatMap((b) => b.changes)) {
    const current = result[change.key] ?? { action: null, note: "" };
    result[change.key] = {
      action: change.action === undefined ? current.action : change.action === "clear" ? null : change.action,
      note: change.note ?? current.note,
    };
  }
  return result;
}
