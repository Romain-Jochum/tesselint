// Trust figures (docs/ui/DESIGN.md §2 "Trust banner"): the o-band chips on every merge and
// overlap card, and the banner's calibration figures (run's calibration.json, else the newest
// same-kind run's, cited, else "not calibrated").

import type { B, N, RunBundle, S } from "./types.ts";

/** One o band of the docs/results.md calibration table: labelled pairs and how many were related. */
export interface OBand {
  lo: N; // inclusive
  hi: N; // exclusive, except the last band, which includes 1
  label: S;
  pairs: N;
  related: N;
}

/** docs/results.md "Calibration", o band table (160 labelled pairs; T1 plus hold-out). */
export const O_BANDS: readonly OBand[] = [
  { lo: 0, hi: 0.5, label: "o < 0.5", pairs: 83, related: 0 },
  { lo: 0.5, hi: 0.7, label: "o 0.5–0.7", pairs: 37, related: 1 },
  { lo: 0.7, hi: 0.9, label: "o 0.7–0.9", pairs: 36, related: 6 },
  { lo: 0.9, hi: 1, label: "o ≥ 0.9", pairs: 4, related: 3 },
];

/** Banner figures and where they came from (§2). */
export interface TrustFigures {
  source: "run" | "cited" | "none"; // this run's calibration.json, another run's, or none
  from: S | null; // the cited run ref when source is "cited"
  values: Record<S, N | B>; // calibration.json fields (rel_kappa, rel_n, atomic_precision, …)
}

/** The band holding o (§2): lower bounds inclusive, 1 (and anything above) in the last band. */
export function bandFor(o: N): OBand {
  const last = O_BANDS[O_BANDS.length - 1]!;
  return O_BANDS.find((band) => o < band.hi) ?? last;
}

/** The card chip for o, e.g. "o 0.7–0.9: 6/36 related" (§2). */
export function bandChip(o: N): S {
  const band = bandFor(o);
  return `${band.label}: ${band.related}/${band.pairs} related`;
}

/** Pick the banner figures from a bundle's calibration and calibration_from (§2). */
export function trustFigures(bundle: Pick<RunBundle, "run" | "calibration" | "calibration_from">): TrustFigures {
  const { run, calibration, calibration_from: from } = bundle;
  if (!calibration) return { source: "none", from: null, values: {} };
  if (from === null || from === run.ref) return { source: "run", from: null, values: calibration };
  return { source: "cited", from, values: calibration };
}
