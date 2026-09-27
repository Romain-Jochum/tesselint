// Trust figures: DESIGN §2 "Trust banner" (the o-band figures of docs/results.md behind "0 of 83
// labelled pairs below 0.5 were related; 9 of 10 related pairs had o >= 0.7"; the chip "o 0.7–0.9:
// 6/36 related"; figures from the run's calibration.json, else another run's (cited), else none).
import { describe, expect, it } from "vitest";
import { O_BANDS, bandChip, bandFor, trustFigures } from "./trust.ts";
import type { RunBundle, RunSummary } from "./types.ts";

describe("O_BANDS (§2 banner figures)", () => {
  it("covers [0, 1] contiguously and holds the banner's figures: 160 pairs, 0 of 83 below 0.5, 9 of 10 related at o >= 0.7", () => {
    expect(O_BANDS[0]!.lo).toBe(0);
    expect(O_BANDS[O_BANDS.length - 1]!.hi).toBe(1);
    for (let i = 1; i < O_BANDS.length; i++) expect(O_BANDS[i]!.lo).toBe(O_BANDS[i - 1]!.hi);
    const sum = (f: (b: (typeof O_BANDS)[number]) => number) => O_BANDS.reduce((acc, b) => acc + f(b), 0);
    expect(sum((b) => b.pairs)).toBe(160);
    expect(O_BANDS[0]).toMatchObject({ pairs: 83, related: 0 });
    expect(sum((b) => b.related)).toBe(10);
    expect(sum((b) => (b.lo >= 0.7 ? b.related : 0))).toBe(9);
  });
});

describe("bandFor and bandChip (§2 card chips)", () => {
  it("puts each o in its band (lower bounds inclusive, 1 in the last band) and formats the §2 chip", () => {
    const table: [number, string][] = [
      [0, "o < 0.5"],
      [0.49, "o < 0.5"],
      [0.5, "o 0.5–0.7"],
      [0.69, "o 0.5–0.7"],
      [0.7, "o 0.7–0.9"],
      [0.9, "o ≥ 0.9"],
      [1, "o ≥ 0.9"],
    ];
    for (const [o, label] of table) expect(bandFor(o).label, String(o)).toBe(label);
    expect(bandChip(0.75)).toBe("o 0.7–0.9: 6/36 related");
    expect(bandChip(0.95)).toBe("o ≥ 0.9: 3/4 related");
    expect(bandChip(0.3)).toBe("o < 0.5: 0/83 related");
  });
});

describe("trustFigures (§2 calibration source)", () => {
  const run = { ref: "t3~20260926-150000" } as RunSummary;
  const calibration = { rel_kappa: 0.11, rel_n: 160, rel_pass: false, atomic_precision: 0.67, atomic_auc: 0.82 };
  const bundle = (c: RunBundle["calibration"], from: string | null) => ({ run, calibration: c, calibration_from: from });

  it("uses the run's own calibration.json", () => {
    expect(trustFigures(bundle(calibration, null))).toEqual({ source: "run", from: null, values: calibration });
    expect(trustFigures(bundle(calibration, run.ref)).source).toBe("run");
  });

  it("cites another run's calibration, else says none", () => {
    expect(trustFigures(bundle(calibration, "t1~20260926-141229"))).toEqual({
      source: "cited",
      from: "t1~20260926-141229",
      values: calibration,
    });
    expect(trustFigures(bundle(null, null))).toEqual({ source: "none", from: null, values: {} });
  });
});
