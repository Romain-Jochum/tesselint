// Heatmap maths (docs/ui/DESIGN.md §5 "Heatmap"; Matrix view §2; packing §7.3 MatrixPacked).
// Pure helpers only: packing, orders, epic aggregates, colours, rasterising and hit tests. The
// Heatmap component paints one ImageData at 1 px per cell, scaled with imageSmoothingEnabled=false.

import type { MatrixPacked, N, Relation, RunBundle, S, StoryRow } from "./types.ts";

/** The dataviz blue ramp, 13 steps light→dark (steps 100–700); reversed in dark mode (§5). */
export const RAMP: readonly S[] = [
  "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
  "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
];

/** Alpha for cells with o < t (§5: drawn at 25 %). */
export const BELOW_T_ALPHA = 0.25;

/** 0–255 channels, alpha included. */
export type RGBA = readonly [N, N, N, N];

/** Matrix row/column order (§2 Matrix "order": document or cluster). */
export type MatrixOrder = "doc" | "cluster";

/** Epic-level metric (§2 Matrix controls: count, share, mean o). */
export type EpicMetric = "count" | "share" | "mean";

/** One Ei × Ej cell (§2 tooltip "E1 × E20 · 14 of 297 pairs ≥ 0.70 · mean o 0.21 · top US-1.1 ⊃ US-20.2 0.99"). */
export interface EpicCell {
  row: S;
  col: S;
  pairs: N; // answered pairs in the block
  above: N; // pairs with o ≥ t
  share: N; // above / pairs (0 when pairs is 0)
  mean_o: N | null;
  top: { a: S; b: S; o: N; relation: Relation | null } | null;
}

/** k × k epic cells, row-major over `epics` (the diagonal holds within-epic pairs). */
export interface EpicGrid {
  epics: S[];
  cells: EpicCell[];
}

/** One story-level pair looked up in either order; relation is re-read with the row story as A. */
export interface CellValue {
  o: N | null;
  m: N | null;
  relation: Relation | null;
}

/** The visible window of the grid (brush zoom) and the plot size in CSS px (§5 hit tests). */
export interface Viewport {
  row0: N; // first visible display row
  col0: N; // first visible display column
  rows: N; // visible rows
  cols: N; // visible columns
  width: N; // plot width, CSS px
  height: N; // plot height, CSS px
}

/** Colours the painter reads from CSS tokens: --cell-missing and --surface-2 (§5). */
export interface HeatColors {
  dark: boolean;
  missing: RGBA;
  diagonal: RGBA;
}

/**
 * Index into MatrixPacked.o/m/rel for stories i ≠ j (document order) of n (§7.3):
 * k = i*n - i*(i+1)/2 + (j-i-1) with i < j; order-insensitive; throws RangeError when i === j.
 */
export function packIndex(i: N, j: N, n: N): N {
  if (i === j) throw new RangeError(`packIndex: the diagonal (${i}, ${j}) has no pair`);
  const [lo, hi] = i < j ? [i, j] : [j, i];
  return lo * n - (lo * (lo + 1)) / 2 + (hi - lo - 1);
}

const MIRROR: Partial<Record<Relation, Relation>> = { A_contains_B: "B_contains_A", B_contains_A: "A_contains_B" };

/** The relation of a packed pair with the row story as A (mirrored when row > col). */
function relationAt(matrix: MatrixPacked, k: N, rowIsA: boolean): Relation | null {
  const index = matrix.rel[k];
  const relation = index === null || index === undefined ? null : (matrix.rel_order[index] ?? null);
  return relation && !rowIsA ? (MIRROR[relation] ?? relation) : relation;
}

/** o, m and relation of (i, j) in document order; relation mirrored (A_contains_B ↔ B_contains_A) when i > j. */
export function cellAt(matrix: MatrixPacked, i: N, j: N): CellValue {
  const k = packIndex(i, j, matrix.ids.length);
  return { o: matrix.o[k] ?? null, m: matrix.m[k] ?? null, relation: relationAt(matrix, k, i < j) };
}

/**
 * Display order as a permutation of document indices (§2): `doc` keeps document order; `cluster`
 * groups cluster members (clusters by mean o, descending), then the unclustered in document order.
 * `stories` is in document order, as RunBundle.stories and MatrixPacked.ids are.
 */
export function orderBy(
  stories: readonly StoryRow[],
  order: MatrixOrder,
  clusters: RunBundle["clusters"],
): N[] {
  const docOrder = stories.map((_, i) => i);
  if (order === "doc") return docOrder;
  const indexOf = new Map(stories.map((s, i) => [s.id, i]));
  const placed = new Set<N>();
  const result: N[] = [];
  const byMean = [...clusters].sort((x, y) => y.mean_overlap - x.mean_overlap);
  for (const cluster of byMean) {
    const members = cluster.members.flatMap((id) => indexOf.get(id) ?? []).sort((x, y) => x - y);
    for (const i of members) {
      if (placed.has(i)) continue;
      placed.add(i);
      result.push(i);
    }
  }
  return [...result, ...docOrder.filter((i) => !placed.has(i))];
}

/** Aggregate the pair matrix into epic blocks at threshold t (§2 Epic level). */
export function epicAggregate(
  matrix: MatrixPacked,
  stories: readonly StoryRow[],
  epics: readonly S[],
  t: N,
): EpicGrid {
  const k = epics.length;
  const epicIndex = new Map(epics.map((e, i) => [e, i]));
  const epicOfId = new Map(stories.map((s) => [s.id, epicIndex.get(s.epic)]));
  const rowEpic = matrix.ids.map((id) => epicOfId.get(id));
  const acc = Array.from({ length: k * k }, () => ({ pairs: 0, above: 0, sum: 0, top: null as EpicCell["top"] }));
  const n = matrix.ids.length;
  const add = (ei: N, ej: N, a: N, b: N, o: N, kk: N) => {
    const cell = acc[ei * k + ej]!;
    cell.pairs += 1;
    cell.sum += o;
    if (o >= t) cell.above += 1;
    if (!cell.top || o > cell.top.o) cell.top = { a: matrix.ids[a]!, b: matrix.ids[b]!, o, relation: relationAt(matrix, kk, a < b) };
  };
  for (let i = 0; i < n; i++) {
    const ei = rowEpic[i];
    if (ei === undefined) continue;
    for (let j = i + 1; j < n; j++) {
      const ej = rowEpic[j];
      const kk = packIndex(i, j, n);
      const o = matrix.o[kk];
      if (ej === undefined || o === null || o === undefined) continue;
      add(ei, ej, i, j, o, kk);
      if (ei !== ej) add(ej, ei, j, i, o, kk);
    }
  }
  const cells = acc.map((c, index): EpicCell => ({
    row: epics[Math.floor(index / k)]!,
    col: epics[index % k]!,
    pairs: c.pairs,
    above: c.above,
    share: c.pairs ? c.above / c.pairs : 0,
    mean_o: c.pairs ? c.sum / c.pairs : null,
    top: c.top,
  }));
  return { epics: [...epics], cells };
}

/** Parse "#rrggbb" (or "#rgb") into RGBA with alpha 255. */
export function hexToRgba(hex: S): RGBA {
  const digits = hex.trim().replace(/^#/, "");
  const full = digits.length === 3 ? [...digits].map((d) => d + d).join("") : digits;
  if (!/^[0-9a-f]{6}$/i.test(full)) throw new RangeError(`hexToRgba: not a hex colour: ${hex}`);
  const channel = (at: N) => parseInt(full.slice(at, at + 2), 16);
  return [channel(0), channel(2), channel(4), 255];
}

const RAMP_RGBA: readonly RGBA[] = RAMP.map(hexToRgba);
const FADED = Math.round(BELOW_T_ALPHA * 255);

/**
 * Colour of a value in [0, 1] (§5): 13-step RAMP (reversed when dark), alpha BELOW_T_ALPHA when
 * value < t, `missing` for null. The steps are 13 equal-width bins of [0, 1].
 */
export function rgbaFor(value: N | null, t: N, colors: HeatColors): RGBA {
  if (value === null || Number.isNaN(value)) return colors.missing;
  const last = RAMP_RGBA.length - 1;
  const step = Math.min(last, Math.max(0, Math.floor(value * RAMP_RGBA.length)));
  const [r, g, b] = RAMP_RGBA[colors.dark ? last - step : step]!;
  return [r, g, b, value < t ? FADED : 255];
}

/**
 * The n × n story-level image in display order (§5): n*n*4 RGBA bytes, row-major, diagonal in
 * colors.diagonal, every other cell rgbaFor(o, t). With `cols`, a rows × cols block instead
 * (the Ei × Ej drill-down), `order` giving the rows.
 */
export function rasterize(
  matrix: MatrixPacked,
  order: readonly N[],
  t: N,
  colors: HeatColors,
  cols: readonly N[] = order,
): Uint8ClampedArray {
  const n = matrix.ids.length;
  const data = new Uint8ClampedArray(order.length * cols.length * 4);
  let at = 0;
  for (const i of order) {
    for (const j of cols) {
      data.set(i === j ? colors.diagonal : rgbaFor(matrix.o[packIndex(i, j, n)] ?? null, t, colors), at);
      at += 4;
    }
  }
  return data;
}

/** Display cell under a pointer at (x, y) CSS px in the plot, or null outside it (§5 arithmetic hit test). */
export function hitTest(x: N, y: N, viewport: Viewport): { row: N; col: N } | null {
  const { row0, col0, rows, cols, width, height } = viewport;
  if (!(x >= 0 && y >= 0 && x < width && y < height) || rows <= 0 || cols <= 0) return null;
  const col = Math.min(cols - 1, Math.floor((x / width) * cols));
  const row = Math.min(rows - 1, Math.floor((y / height) * rows));
  return { row: row0 + row, col: col0 + col };
}

/** A run of consecutive display rows (or columns) sharing a key, e.g. an epic (§2 "epic boundaries"). */
export interface AxisGroup {
  key: S | null; // null: rows with no key (unclustered stories)
  start: N; // first display index
  count: N;
}

/** Consecutive runs of `labels` with the same `keyOf`; boundaries fall at each group's start. */
export function axisGroups(labels: readonly S[], keyOf: (label: S) => S | null): AxisGroup[] {
  const groups: AxisGroup[] = [];
  labels.forEach((label, index) => {
    const key = keyOf(label);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.count += 1;
    else groups.push({ key, start: index, count: 1 });
  });
  return groups;
}
