// Selector contract for the tests (docs/ui/DESIGN.md §6). Every element a Vitest component test or
// a Playwright spec touches carries one of these `data-testid` values; implementers must use them
// verbatim (`data-testid={TID.caseRow}`). Parametrised IDs are functions. Row-like elements also
// carry the ATTR data attributes below, so specs can read keys, statuses and values without
// parsing visible text.

import type { Kind, S } from "./types.ts";

/** Overview tiles (§2 Overview "Tiles"). */
export type TileName = "stories" | "merges" | "overlaps" | "splits" | "decided" | "cost";

/** Run diff sections (§2 Runs; §7.3 RunDiff), one list each. */
export type DiffSection =
  | "added"
  | "removed"
  | "edited"
  | "renamed"
  | "new"
  | "gone"
  | "changed"
  | "decisions_to_review";

/** "What changed vs the current source" lists on Overview (§2 Overview; RunBundle.source_status). */
export type SourceChange = "edited" | "added" | "removed";

/** Data attributes carried next to the test IDs. */
export const ATTR = {
  /** Identity of a row: case key, proposal key, story ID, run ref (renamed diff rows: `old>new`). */
  key: "data-key",
  /** Case row status (`undecided|stale|decided`, lib/cases.ts CaseStatus); option status
   * (`accept|reject|defer|superseded|none`). */
  status: "data-status",
  /** Shared-phrase run index on a <mark> (§5 Phrases); twins share it. */
  run: "data-run",
  /** "true" on both twins of the hovered shared phrase (§5 "hovering one lights its twin"). */
  active: "data-active",
  /** A row's numeric value, e.g. a neighbour's o (§2 Story). */
  value: "data-value",
  /** Heatmap root: comma-joined row / column labels in display order (§2 Matrix order). */
  rows: "data-rows",
  cols: "data-cols",
} as const;

export const TID = {
  // ---- Chrome (§2 "Chrome") ----
  header: "header",
  runSwitcher: "run-switcher",
  /** The `real` / `synthetic` badge; its text is RunSummary.kind. */
  runKind: "run-kind",
  keyStatus: "key-status",
  /** Amber `source edited: N` chip, a link to Re-run. */
  sourceEditedChip: "source-edited",
  /** `saved · rev N`. */
  savedRev: "saved-rev",
  themeToggle: "theme-toggle",
  /** Tabs: overview, review, matrix, clusters, runs, export. */
  tab: (name: S) => `tab-${name}`,
  /** The `/` search box; Enter opens the top result. */
  searchInput: "search-input",

  // ---- Trust banner (§2) ----
  trustBanner: "trust-banner",
  /** Collapse/expand button; carries aria-expanded. Never a close button. */
  trustToggle: "trust-toggle",
  /** The o-band chip on merge and overlap cards, e.g. "o 0.7–0.9: 6/36 related". */
  bandChip: "band-chip",

  // ---- Overview (§2) ----
  tile: (name: TileName) => `tile-${name}`,
  /** A "what changed vs the current source" list; items carry ATTR.key = story ID. */
  sourceChanges: (change: SourceChange) => `source-${change}`,

  // ---- Review (§2) ----
  caseList: "case-list",
  /** One case row: ATTR.key = case key, ATTR.status = CaseStatus. */
  caseRow: "case-row",
  casePane: "case-pane",
  /** Option n (1-based, as keys 1–9 pick it) of the open case: ATTR.key = proposal key,
   * ATTR.status = its action or `superseded`; its text says "superseded" when derived so. */
  option: (n: number) => `option-${n}`,
  /** "US-1.1 is split S4 · undecided · c". */
  conflictStrip: "conflict-strip",
  /** Word diff of stored against current text (stale cases, edited stories): <del> and <ins>. */
  wordDiff: "word-diff",
  /** SideBySide columns: story A (left) and story B (right). */
  sideA: "side-a",
  sideB: "side-b",
  /** Merge/split/overlap evidence block (relation, m, o, fwd/swap bars…). */
  evidence: "evidence",
  /** Kind filter counts under the current thresholds: proposals of that kind. */
  kindCount: (kind: Kind) => `count-${kind}`,
  /** Status filter links (open, stale, decided, all, orphaned). */
  statusFilter: (status: S) => `status-${status}`,
  /** Orphaned decisions list (`status=orphaned`); rows carry ATTR.key = decision key. */
  orphanList: "orphan-list",

  // ---- DecisionBar (§2 Review keys) ----
  decisionBar: "decision-bar",
  acceptButton: "decide-accept",
  rejectButton: "decide-reject",
  rejectAllButton: "decide-reject-all",
  deferButton: "decide-defer",
  noteButton: "decide-note",
  undoButton: "decide-undo",
  /** The note textarea (Ctrl+Enter saves, Esc cancels). */
  noteInput: "note-input",
  /** Shown after a failed save (§2 "rolls back and offers Retry"). */
  retryButton: "decide-retry",
  /** The aria-live region: "Accepted merge US-14.5 into US-1.1 · u to undo". */
  liveRegion: "live-region",

  // ---- Matrix and Heatmap (§2 Matrix, §5 Heatmap) ----
  /** Heatmap root; carries ATTR.rows / ATTR.cols. */
  heatmap: "heatmap",
  /** The ImageData canvas: 2d context, intrinsic size cols × rows (1 px per cell). */
  heatmapBase: "heatmap-base",
  /** The overlay canvas on top (hover, focus, brush); receives pointer events. */
  heatmapOverlay: "heatmap-overlay",
  /** Hover tooltip; filled with textContent. */
  heatmapTooltip: "heatmap-tooltip",
  level: (level: "epics" | "stories") => `level-${level}`,
  order: (order: "doc" | "cluster") => `order-${order}`,
  /** The t slider (<input type="range">, 0–1). */
  tSlider: "t-slider",
  matrixTable: "matrix-table",
  pairDrawer: "pair-drawer",

  // ---- Story (§2 Other views) ----
  /** Neighbour rows: ATTR.key = story ID, ATTR.value = o. */
  neighbour: "neighbour",

  // ---- Runs and diff (§2 Runs) ----
  /** A run row: ATTR.key = run ref. */
  runRow: "run-row",
  /** The tick box of a run row: ATTR.key = run ref. */
  runTick: "run-tick",
  /** A diff list; items carry ATTR.key (renamed rows: `old>new`). */
  diffList: (section: DiffSection) => `diff-${section}`,

  // ---- Re-run and Job (§2 Re-run) ----
  estimateButton: "estimate",
  estimatePanel: "estimate-panel",
  /** The exact command the estimate would run. */
  estimateCommand: "estimate-command",
  /** Cap in US$ (<input type="number">), defaulted per §2 step 3. */
  capInput: "cap-input",
  /** "I accept spending up to US$X". */
  spendTick: "spend-tick",
  startButton: "start-job",
  /** Job.state as text. */
  jobState: "job-state",
  /** The exit-code message (§2 Re-run). */
  jobMessage: "job-message",
  /** Link to the finished run, /r/<run_ref>/overview. */
  jobRunLink: "job-run-link",
  cancelButton: "cancel-job",

  // ---- Export (§2 Export) ----
  exportPreview: "export-preview",
  downloadMd: "export-download-md",
  downloadJson: "export-download-json",
  /** Writes both files to <state>/exports/. */
  writeExports: "export-write",
  /** Shows md_path and json_path after a write. */
  exportWritten: "export-written",
} as const;
