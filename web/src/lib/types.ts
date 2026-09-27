// API types (docs/ui/DESIGN.md §7.3, binding; every type is exported). The block after the §7.3
// types names the §7.2 request and response shapes that §7.3 leaves inline (additive).

export type S = string;
export type N = number;
export type B = boolean;
export type Kind = "merge" | "overlap" | "split";
export type Action = "accept" | "reject" | "defer";

export type Relation =
  | "duplicate"
  | "A_contains_B"
  | "B_contains_A"
  | "partial_overlap"
  | "same_area_distinct"
  | "unrelated";
export type Probs = Partial<Record<Relation, N>>;

/** The eight models.Thresholds fields (swap_gate, merge, cluster, split, split_corroborated, related, min_words_atomic, borderline). */
export type Thresholds = Record<S, N>;

export interface RunSummary {
  ref: S;
  kind: "real" | "synthetic";
  kind_inferred: B;
  dry_run: B;
  status: S;
  source: { path: S | null; format: "md" | "json" | null };
  tier: S | null;
  n_stories: N;
  finished_at: S | null;
  cost_live_usd: N;
  cost_cached_usd: N;
  snapshot_sha256: S;
  proposals: Record<Kind, N>;
}

export interface StoryRow {
  id: S;
  epic: S;
  title: S;
  tag: S;
  words: N;
  text_hash: S;
  text: S;
  atomic: N | null;
  order: N;
}

export interface Edge {
  a: S;
  b: S;
  overlap: N;
  merge: N;
  relation: Relation;
}

export interface RunBundle {
  run: RunSummary;
  thresholds: Thresholds;
  epics: { id: S; name: S }[];
  stories: StoryRow[];
  tombstones: { id: S; title: S; target: S | null }[];
  clusters: { id: S; members: S[]; mean_overlap: N; edges: Edge[] }[];
  previous: S | null;
  calibration: Record<S, N | B> | null;
  calibration_from: S | null;
  source_status: {
    source_id: N | null;
    same_snapshot: B;
    edited: S[];
    added: S[];
    removed: S[];
    problems: S[];
  };
}

/**
 * Upper triangle, row-major, i < j in document order: k = i*n - i*(i+1)/2 + (j-i-1);
 * rel indexes rel_order with ids[i] as A.
 */
export interface MatrixPacked {
  ids: S[];
  o: (N | null)[];
  m: (N | null)[];
  rel: (N | null)[];
  rel_order: Relation[];
}

export interface Base {
  id: S;
  key: S;
  target: S | null;
  stories: S[];
  decisive_value: N;
  borderline: B;
  conflicts: S[];
}

export interface MergeDetails {
  relation: "duplicate" | "containment";
  keep: S;
  drop: S;
  m: N;
  o: N;
  fwd: Probs;
  swap: Probs | null;
  cluster_keep: S | null;
  cluster_drop: S | null;
  cos: N | null;
  atomic_keep: N | null;
  atomic_drop: N | null;
  related: Edge[];
  alternatives: S[];
}

export interface OverlapDetails {
  cluster: S;
  members: S[];
  mean_o: N;
  edges: Edge[];
  related: Edge[];
}

export interface SplitDetails {
  story: S;
  bundled: N;
  words: N;
  n_subs: N;
  cluster: S | null;
  reaches: S[];
  keeps: S[];
}

export type Proposal =
  | (Base & { kind: "merge"; details: MergeDetails })
  | (Base & { kind: "overlap"; details: OverlapDetails })
  | (Base & { kind: "split"; details: SplitDetails });

export interface Case {
  key: S;
  kind: Kind;
  anchor: S;
  proposals: S[];
  strength: N;
  borderline: B;
  epics: S[];
  conflicts: S[];
  order: N;
}

export interface ProposalsResponse {
  thresholds: Thresholds;
  proposals: Proposal[];
  cases: Case[];
  hubs: { story: S; keeps: S[]; split: S | null }[];
}

export interface PairDetail {
  a: S;
  b: S;
  overlap: N;
  merge: N;
  relation: Relation;
  fwd: Probs;
  swap: Probs | null;
  cos: N | null;
  proposals: S[];
}

export interface Decision {
  key: S;
  kind: Kind;
  target: S | null;
  stories: S[];
  action: Action | null;
  note: S;
  text_hashes: Record<S, S>;
  titles: Record<S, S>;
  /** The proposal's decisive value when decided (PLAN resolution 2): m, bundled score or mean o. */
  decisive_value: N;
  run: S;
  decided_at: S;
}

/**
 * A Decision plus the per-run computed fields (§3). The server always sends `decisive_value`;
 * it is optional here only because the §7.3-era test fixtures (cases.test.ts) omit it.
 */
export interface DecisionView extends Omit<Decision, "decisive_value"> {
  decisive_value?: N;
  status: "current" | "stale" | "missing";
  changed: S[];
  proposed: B;
  superseded_by: S | null;
  applied: B | null;
  old_texts?: Record<S, S>;
}

export interface DecisionsResponse {
  rev: N;
  decisions: DecisionView[];
}

export interface DecisionChange {
  key: S;
  action?: Action | "clear";
  note?: S;
}

export interface RunDiff {
  added: S[];
  removed: S[];
  renamed: [S, S][];
  edited: { id: S; old_text: S; new_text: S }[];
  new: S[];
  gone: S[];
  changed: S[];
  decisions_to_review: S[];
}

/** Same shape as `tesselint key --json`. */
export interface KeyStatus {
  present: B;
  usage_usd: N | null;
  limit_usd: N | null;
  remaining_usd: N | null;
  error: S | null;
}

export interface Estimate {
  id: S;
  to_ask: N;
  cached: N;
  usd: N;
  eta_s: N;
  command: S;
  expires_at: S;
  key: KeyStatus;
  max_usd_allowed: N;
}

export interface Job {
  id: S;
  state: "running" | "done" | "failed" | "cancelled" | "lost";
  exit_code: N | null;
  message: S | null;
  run_ref: S | null;
  max_usd: N;
  spent_usd: N;
  log_tail: S[];
  phases: { name: S; done: N; total: N }[];
}

// ---- §7.2 shapes that §7.3 leaves inline (additive names) ----

/** Error codes named in §7.2 (plus the §4.1 guard and §3 store codes). */
export type ErrorCode =
  | "run_not_found"
  | "job_running"
  | "estimate_stale"
  | "nothing_to_undo"
  | "bad_threshold"
  | "unknown_key"
  | "cap_invalid"
  | "cli_failed"
  | "bad_host"
  | "csrf"
  | "decisions_unreadable";

/** Every API error body: {"error": {"code", "message"}}. */
export interface ApiErrorBody {
  error: { code: ErrorCode | S; message: S };
}

/** An allowlisted source (§4.1); `id` is the `source_id` used by estimates and `?source=`. */
export interface SourceInfo {
  id: N;
  path: S;
  format: "md" | "json";
  exists: B;
}

/** GET /api/health */
export interface Health {
  version: S;
  build: S;
  sources: SourceInfo[];
  ui_max_usd: N;
}

/** GET /api/runs?include=dry */
export interface RunsResponse {
  latest: S | null;
  runs: RunSummary[];
}

/** Threshold overrides sent as ?merge&cluster&split and encoded in the Review `th` param. */
export interface ThOverrides {
  merge?: N;
  cluster?: N;
  split?: N;
}

/** POST /api/decisions */
export interface DecisionsRequest {
  run: S;
  thresholds?: ThOverrides;
  changes: DecisionChange[];
}

/** POST /api/exports -> 201 */
export interface ExportWritten {
  md_path: S;
  json_path: S;
}

/** POST /api/estimates -> 201 Estimate */
export interface EstimateRequest {
  source_id: N;
  tier?: S;
  epics?: S[];
  resume?: S;
}

/** POST /api/jobs -> 202 Job */
export interface JobStartRequest {
  estimate_id: S;
  max_usd: N;
  confirm: "spend";
}
