// Epic health (docs/ui/DESIGN.md §2 Overview "Epic health: a sortable table of live stories,
// stories in proposals, groups/overlaps/splits, within-epic pairs with o ≥ t, top partner epic,
// share decided"). Pure: the Overview passes the bundle, proposals, cases and matrix.

import { epicAggregate } from "./matrix.ts";
import type { Case, MatrixPacked, N, Proposal, RunBundle, S } from "./types.ts";

export interface EpicHealthRow {
  epic: S;
  name: S;
  order: N; // document order of the epic
  live: N; // stories in the run
  inProposals: N; // distinct stories of the epic named by a proposal
  merges: N; // merge groups touching the epic
  overlaps: N;
  splits: N;
  within: N; // within-epic pairs with o ≥ t
  partner: S | null; // the other epic with the most pairs ≥ t
  partnerPairs: N;
  cases: N; // cases touching the epic
  decided: N;
  share: N | null; // decided / cases; null without cases
}

export type HealthKey = "epic" | "live" | "inProposals" | "merges" | "overlaps" | "splits" | "within" | "partnerPairs" | "share";

export function epicHealth(
  bundle: Pick<RunBundle, "epics" | "stories">,
  proposals: readonly Proposal[],
  cases: readonly Case[],
  matrix: MatrixPacked | null,
  t: N,
  isDecided: (c: Case) => boolean,
): EpicHealthRow[] {
  const epicOf = new Map(bundle.stories.map((s) => [s.id, s.epic]));
  const ids = bundle.epics.map((e) => e.id);
  const grid = matrix ? epicAggregate(matrix, bundle.stories, ids, t) : null;
  const named = new Set(proposals.flatMap((p) => p.stories));
  return bundle.epics.map((epic, index) => {
    const touching = cases.filter((c) => c.epics.includes(epic.id));
    const count = (kind: Case["kind"]) => touching.filter((c) => c.kind === kind).length;
    const decided = touching.filter(isDecided).length;
    const row = grid?.cells.slice(index * ids.length, (index + 1) * ids.length) ?? [];
    const partner = row
      .filter((cell) => cell.col !== epic.id && cell.above > 0)
      .reduce<(typeof row)[number] | null>((best, cell) => (!best || cell.above > best.above ? cell : best), null);
    return {
      epic: epic.id,
      name: epic.name,
      order: index,
      live: bundle.stories.filter((s) => s.epic === epic.id).length,
      inProposals: [...named].filter((id) => epicOf.get(id) === epic.id).length,
      merges: count("merge"),
      overlaps: count("overlap"),
      splits: count("split"),
      within: row[index]?.above ?? 0,
      partner: partner?.col ?? null,
      partnerPairs: partner?.above ?? 0,
      cases: touching.length,
      decided,
      share: touching.length ? decided / touching.length : null,
    };
  });
}

/** Sort rows by a column; `epic` sorts by document order, nulls (no cases) always last. */
export function sortHealth(rows: readonly EpicHealthRow[], key: HealthKey, dir: "asc" | "desc"): EpicHealthRow[] {
  const sign = dir === "asc" ? 1 : -1;
  const value = (r: EpicHealthRow): N | null => (key === "epic" ? r.order : r[key]);
  return [...rows].sort((x, y) => {
    const [a, b] = [value(x), value(y)];
    if (a === null || b === null) return a === b ? x.order - y.order : a === null ? 1 : -1;
    return sign * (a - b) || x.order - y.order;
  });
}
