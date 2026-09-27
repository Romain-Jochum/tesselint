// Heatmap maths: DESIGN §7.3 MatrixPacked packing formula, §5 "Heatmap" (ramp, missing, diagonal,
// 25 % alpha below t, arithmetic hit tests), §2 Matrix (orders, epic metrics and tooltip figures).
import { describe, expect, it } from "vitest";
import {
  BELOW_T_ALPHA,
  RAMP,
  cellAt,
  epicAggregate,
  hexToRgba,
  hitTest,
  orderBy,
  packIndex,
  rasterize,
  rgbaFor,
  type HeatColors,
  type RGBA,
} from "./matrix.ts";
import type { MatrixPacked, Relation, RunBundle, StoryRow } from "./types.ts";

const REL_ORDER: Relation[] = [
  "duplicate",
  "A_contains_B",
  "B_contains_A",
  "partial_overlap",
  "same_area_distinct",
  "unrelated",
];

function story(id: string, epic: string, order: number): StoryRow {
  return { id, epic, title: `Title ${id}`, tag: "", words: 20, text_hash: "0".repeat(64), text: id, atomic: null, order };
}

// Five stories, epics E1 = A B C and E2 = D E. Pairs in packed order:
//      AB    AC    AD    AE    BC    BD    BE    CD    CE    DE
const FIVE: MatrixPacked = {
  ids: ["A", "B", "C", "D", "E"],
  o: [0.9, 0.2, 0.75, null, 0.4, 0.1, 0.3, null, 0.95, 0.6],
  m: [0.85, 0.1, 0.6, null, 0.3, 0.05, 0.2, null, 0.9, 0.5],
  rel: [1, 5, 3, null, 4, 5, 5, null, 0, 3],
  rel_order: REL_ORDER,
};
const FIVE_STORIES = [story("A", "E1", 0), story("B", "E1", 1), story("C", "E1", 2), story("D", "E2", 3), story("E", "E2", 4)];

const LIGHT: HeatColors = { dark: false, missing: [1, 2, 3, 255], diagonal: [4, 5, 6, 255] };
const DARK: HeatColors = { dark: true, missing: [7, 8, 9, 255], diagonal: [10, 11, 12, 255] };
const FIRST: RGBA = [0xcd, 0xe2, 0xfb, 255]; // #cde2fb
const LAST: RGBA = [0x0d, 0x36, 0x6b, 255]; // #0d366b

describe("packIndex (§7.3 k = i*n - i*(i+1)/2 + (j-i-1))", () => {
  it("numbers the upper triangle row-major from 0 without gaps", () => {
    const n = 7;
    const seen: number[] = [];
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) seen.push(packIndex(i, j, n));
    expect(seen).toEqual(Array.from({ length: (n * (n - 1)) / 2 }, (_, k) => k));
    expect(packIndex(0, 1, 176)).toBe(0);
    expect(packIndex(1, 2, 4)).toBe(3);
    expect(packIndex(174, 175, 176)).toBe((176 * 175) / 2 - 1);
  });

  it("is order-insensitive and throws RangeError on the diagonal", () => {
    expect(packIndex(4, 2, 9)).toBe(packIndex(2, 4, 9));
    expect(() => packIndex(3, 3, 9)).toThrow(RangeError);
  });
});

describe("cellAt (§7.3 rel indexes rel_order with ids[i] as A)", () => {
  it("reads o, m and relation for i < j", () => {
    expect(cellAt(FIVE, 0, 1)).toEqual({ o: 0.9, m: 0.85, relation: "A_contains_B" });
    expect(cellAt(FIVE, 2, 4)).toEqual({ o: 0.95, m: 0.9, relation: "duplicate" });
  });

  it("mirrors containment when the row story is B", () => {
    expect(cellAt(FIVE, 1, 0)).toEqual({ o: 0.9, m: 0.85, relation: "B_contains_A" });
    expect(cellAt(FIVE, 3, 0)).toEqual({ o: 0.75, m: 0.6, relation: "partial_overlap" });
    expect(cellAt(FIVE, 0, 4)).toEqual({ o: null, m: null, relation: null });
    expect(cellAt(FIVE, 4, 0)).toEqual({ o: null, m: null, relation: null });
  });
});

describe("orderBy (§2 Matrix order)", () => {
  const stories = ["S0", "S1", "S2", "S3", "S4", "S5"].map((id, i) => story(id, "E1", i));
  const clusters: RunBundle["clusters"] = [
    { id: "C1", members: ["S1", "S4"], mean_overlap: 0.6, edges: [] },
    { id: "C2", members: ["S2", "S5"], mean_overlap: 0.8, edges: [] },
  ];

  it("doc keeps document order", () => {
    expect(orderBy(stories, "doc", clusters)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("cluster groups members by cluster mean o, then the unclustered in document order", () => {
    expect(orderBy(stories, "cluster", clusters)).toEqual([2, 5, 1, 4, 0, 3]);
    expect(orderBy(stories, "cluster", [])).toEqual([0, 1, 2, 3, 4, 5]);
  });
});

describe("epicAggregate (§2 Epic level: count, share, mean o, top)", () => {
  const grid = () => epicAggregate(FIVE, FIVE_STORIES, ["E1", "E2"], 0.75);
  const cell = (row: string, col: string) => {
    const found = grid().cells.find((c) => c.row === row && c.col === col);
    if (!found) throw new Error(`no cell ${row} × ${col}`);
    return found;
  };

  it("lays out k × k cells row-major and counts answered within-epic pairs and those with o >= t", () => {
    const g = grid();
    expect(g.epics).toEqual(["E1", "E2"]);
    expect(g.cells.map((c) => `${c.row}×${c.col}`)).toEqual(["E1×E1", "E1×E2", "E2×E1", "E2×E2"]);
    const c = cell("E1", "E1");
    expect([c.pairs, c.above]).toEqual([3, 1]);
    expect(c.share).toBeCloseTo(1 / 3);
    expect(c.mean_o).toBeCloseTo(0.5);
    expect(c.top).toMatchObject({ a: "A", b: "B", o: 0.9 });
  });

  it("counts cross-epic pairs, skips unanswered ones and counts o === t as above", () => {
    const c = cell("E1", "E2");
    expect([c.pairs, c.above]).toEqual([4, 2]);
    expect(c.share).toBeCloseTo(0.5);
    expect(c.mean_o).toBeCloseTo(0.525);
    expect(c.top).toMatchObject({ o: 0.95 });
    expect([c.top?.a, c.top?.b].sort()).toEqual(["C", "E"]);
    const [ab, ba] = [c, cell("E2", "E1")];
    expect([ba.pairs, ba.above, ba.mean_o]).toEqual([ab.pairs, ab.above, ab.mean_o]);
  });

  it("gives share 0, mean null and no top to a block without answered pairs", () => {
    const two: MatrixPacked = { ids: ["A", "B"], o: [0.5], m: [0.4], rel: [3], rel_order: REL_ORDER };
    const g = epicAggregate(two, [story("A", "E1", 0), story("B", "E2", 1)], ["E1", "E2"], 0.7);
    expect(g.cells[0]).toMatchObject({ row: "E1", col: "E1", pairs: 0, above: 0, share: 0, mean_o: null, top: null });
    expect(g.cells[1]).toMatchObject({ pairs: 1, above: 0, share: 0, mean_o: 0.5 });
  });
});

describe("colours (§5 ramp #cde2fb → #0d366b, reversed in dark mode)", () => {
  it("maps 0 and 1 to the ramp ends and 0.5 to the middle step", () => {
    expect(hexToRgba("#0d366b")).toEqual([13, 54, 107, 255]);
    expect(hexToRgba("#FFFFFF")).toEqual([255, 255, 255, 255]);
    expect(RAMP).toHaveLength(13);
    expect(rgbaFor(0, 0, LIGHT)).toEqual(FIRST);
    expect(rgbaFor(1, 0, LIGHT)).toEqual(LAST);
    expect(rgbaFor(0.5, 0, LIGHT)).toEqual(hexToRgba(RAMP[6]!));
  });

  it("reverses the ramp in dark mode and uses the missing colour for null", () => {
    expect(rgbaFor(0, 0, DARK)).toEqual(LAST);
    expect(rgbaFor(1, 0, DARK)).toEqual(FIRST);
    expect(rgbaFor(null, 0.7, LIGHT)).toEqual(LIGHT.missing);
    expect(rgbaFor(null, 0.7, DARK)).toEqual(DARK.missing);
  });

  it("draws o < t at 25 % alpha with the same hue, and o === t fully opaque", () => {
    const faded = rgbaFor(0.6, 0.7, LIGHT);
    const opaque = rgbaFor(0.6, 0, LIGHT);
    expect(faded.slice(0, 3)).toEqual(opaque.slice(0, 3));
    expect(Math.abs(faded[3] - BELOW_T_ALPHA * 255)).toBeLessThanOrEqual(1);
    expect(rgbaFor(0.7, 0.7, LIGHT)[3]).toBe(255);
  });
});

describe("rasterize (§5 one ImageData, 1 px per cell)", () => {
  const three: MatrixPacked = { ids: ["A", "B", "C"], o: [0.9, null, 0.3], m: [0.8, null, 0.2], rel: [0, null, 3], rel_order: REL_ORDER };
  const px = (data: Uint8ClampedArray, n: number, r: number, c: number) => Array.from(data.slice((r * n + c) * 4, (r * n + c) * 4 + 4));

  it("paints n × n RGBA in display order with a masked diagonal, missing pairs and faded cells", () => {
    const data = rasterize(three, [0, 1, 2], 0.5, LIGHT);
    expect(data).toHaveLength(3 * 3 * 4);
    for (const i of [0, 1, 2]) expect(px(data, 3, i, i)).toEqual([...LIGHT.diagonal]);
    expect(px(data, 3, 0, 1)).toEqual([...rgbaFor(0.9, 0.5, LIGHT)]);
    expect(px(data, 3, 1, 0)).toEqual(px(data, 3, 0, 1));
    expect(px(data, 3, 0, 2)).toEqual([...LIGHT.missing]);
    expect(px(data, 3, 1, 2)).toEqual([...rgbaFor(0.3, 0.5, LIGHT)]);
    const reversed = rasterize(three, [2, 1, 0], 0.5, LIGHT);
    expect(px(reversed, 3, 0, 1)).toEqual([...rgbaFor(0.3, 0.5, LIGHT)]); // C × B
    expect(px(reversed, 3, 1, 2)).toEqual([...rgbaFor(0.9, 0.5, LIGHT)]); // B × A
  });
});

describe("hitTest (§5 arithmetic hit tests, no DOM per cell)", () => {
  const full = { row0: 0, col0: 0, rows: 10, cols: 10, width: 200, height: 100 };

  it("maps a point to its cell", () => {
    expect(hitTest(0, 0, full)).toEqual({ row: 0, col: 0 });
    expect(hitTest(45, 35, full)).toEqual({ row: 3, col: 2 });
    expect(hitTest(199.9, 99.9, full)).toEqual({ row: 9, col: 9 });
    expect(hitTest(200, 50, full)).toBeNull();
    expect(hitTest(50, 100, full)).toBeNull();
    expect(hitTest(-0.1, 5, full)).toBeNull();
  });

  it("offsets by the zoomed window", () => {
    expect(hitTest(45, 35, { ...full, row0: 5, col0: 3 })).toEqual({ row: 8, col: 5 });
    expect(hitTest(45, 35, { row0: 2, col0: 2, rows: 4, cols: 4, width: 200, height: 100 })).toEqual({ row: 3, col: 2 });
  });
});
