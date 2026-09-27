// Router contract: DESIGN §5 "parseLocation() and href(route) … must round-trip"; routes and URL
// params from §2 (Routes, Review URL, Matrix URL, Runs, Re-run, Export, Clusters).
import { describe, expect, it } from "vitest";
import { formatTh, href, parseLocation, parseTh, type Route } from "./router.ts";

const RUN = "mini~20260102-090000";

/** Parse an href the way the browser would hand it to parseLocation. */
function parseHref(h: string): Route {
  const url = new URL(h, "http://127.0.0.1:8765");
  return parseLocation({ pathname: url.pathname, search: url.search });
}

// Every §2 route, each with every search param it defines.
const ROUTES: [string, Route][] = [
  ["home", { name: "home" }],
  ["overview", { name: "overview", run: RUN }],
  ["review, no params", { name: "review", run: RUN, search: {} }],
  [
    "review, every param",
    {
      name: "review",
      run: RUN,
      search: {
        case: "mg:US-14.5",
        opt: 2,
        kind: "merge",
        epic: "E3",
        min: 0.55,
        bl: "only",
        status: "stale",
        preset: "o70",
        sort: "doc",
        th: { merge: 0.5, cluster: 0.7, split: 0.8 },
      },
    },
  ],
  ["review, orphaned", { name: "review", run: RUN, search: { status: "orphaned", bl: "hide" } }],
  ["review, overlap case key", { name: "review", run: RUN, search: { case: "overlap:US-1.1+US-2.3+US-7.1" } }],
  ["review, split case key", { name: "review", run: RUN, search: { case: "split:US-17.3", kind: "split" } }],
  [
    "matrix, every param",
    {
      name: "matrix",
      run: RUN,
      search: {
        level: "stories",
        order: "cluster",
        t: 0.7,
        rows: "E1",
        cols: "E20",
        pair: ["US-1.1", "US-20.2"],
        view: "table",
      },
    },
  ],
  ["matrix, no params", { name: "matrix", run: RUN, search: {} }],
  ["clusters", { name: "clusters", run: RUN, search: { c: "C5" } }],
  ["export, include_stale", { name: "export", run: RUN, search: { include_stale: true } }],
  ["export, no params", { name: "export", run: RUN, search: {} }],
  ["story", { name: "story", run: RUN, id: "US-14.5" }],
  ["epic", { name: "epic", run: RUN, id: "E12" }],
  ["runs", { name: "runs", search: {} }],
  ["runs, include=dry", { name: "runs", search: { include: "dry" } }],
  ["runs diff", { name: "runDiff", search: { a: "mini~20260101-090000", b: RUN } }],
  ["re-run, source 0 and tier", { name: "reRun", search: { source: 0, tier: "t3" } }],
  ["re-run, resume", { name: "reRun", search: { resume: "t1~20260926-141229" } }],
  ["job", { name: "job", id: "job-20260926-1" }],
];

describe("router round trip (§5)", () => {
  it("parseLocation(href(r)) deep-equals r for every route and search param", () => {
    for (const [label, route] of ROUTES) expect(parseHref(href(route)), label).toEqual(route);
  });

  it("href gives the §2 paths, with no `?` for an empty search", () => {
    const paths: [string, Route][] = [
      ["/", { name: "home" }],
      [`/r/${RUN}/overview`, { name: "overview", run: RUN }],
      [`/r/${RUN}/story/US-1.1`, { name: "story", run: RUN, id: "US-1.1" }],
      [`/r/${RUN}/epic/E1`, { name: "epic", run: RUN, id: "E1" }],
      ["/runs/job/j-7", { name: "job", id: "j-7" }],
      [`/r/${RUN}/review`, { name: "review", run: RUN, search: {} }],
      ["/runs", { name: "runs", search: {} }],
    ];
    for (const [path, route] of paths) expect(href(route)).toBe(path);
  });

  it("parses the §2 example URLs; absent params stay undefined (no defaults while parsing)", () => {
    expect(parseLocation({ pathname: "/runs/new", search: "?source=0&tier=t3" })).toEqual({
      name: "reRun",
      search: { source: 0, tier: "t3" },
    });
    expect(parseLocation({ pathname: `/r/${RUN}/matrix`, search: "?pair=US-1.1~US-20.2" })).toEqual({
      name: "matrix",
      run: RUN,
      search: { pair: ["US-1.1", "US-20.2"] },
    });
    expect(parseLocation({ pathname: "/runs/diff", search: `?a=x&b=${RUN}` })).toEqual({
      name: "runDiff",
      search: { a: "x", b: RUN },
    });
    expect(parseLocation({ pathname: `/r/${RUN}/review`, search: "" })).toEqual({ name: "review", run: RUN, search: {} });
  });

  it("drops bad param values instead of failing; unknown paths are notFound with the path (§5)", () => {
    const route = parseLocation({
      pathname: `/r/${RUN}/review`,
      search: "?status=bogus&opt=abc&kind=dupe&bl=some&sort=size&preset=o50&case=mg:US-2.1",
    });
    expect(route).toEqual({ name: "review", run: RUN, search: { case: "mg:US-2.1" } });
    const matrix = parseLocation({ pathname: `/r/${RUN}/matrix`, search: "?level=cells&pair=US-1.1&t=high" });
    expect(matrix).toEqual({ name: "matrix", run: RUN, search: {} });
    expect(parseLocation({ pathname: "/nope/x", search: "" })).toEqual({ name: "notFound", path: "/nope/x" });
    expect(parseLocation({ pathname: `/r/${RUN}/bogus`, search: "" })).toMatchObject({ name: "notFound" });
  });
});

describe("th param (§2 `th=m0.5,c0.7,s0.8`)", () => {
  it("parses all three overrides or a subset in any order", () => {
    expect(parseTh("m0.5,c0.7,s0.8")).toEqual({ merge: 0.5, cluster: 0.7, split: 0.8 });
    expect(parseTh("m0.9")).toEqual({ merge: 0.9 });
    expect(parseTh("s0.75,c0.6")).toEqual({ cluster: 0.6, split: 0.75 });
  });

  it("gives null for malformed values", () => {
    for (const value of ["", "m", "x0.5", "m0.5,,c0.7", "mfoo", "m1.5", "m-0.1"]) expect(parseTh(value), value).toBeNull();
  });

  it("formats in m, c, s order and round-trips", () => {
    expect(formatTh({ split: 0.8, merge: 0.5 })).toBe("m0.5,s0.8");
    expect(formatTh({ cluster: 0.7, split: 0.8, merge: 0.5 })).toBe("m0.5,c0.7,s0.8");
    const th = { merge: 0.62, cluster: 0.7 };
    expect(parseTh(formatTh(th))).toEqual(th);
  });

  it("the review URL accepts th with encoded commas", () => {
    const route = parseLocation({ pathname: `/r/${RUN}/review`, search: "?th=m0.9%2Cs0.8" });
    expect(route).toEqual({ name: "review", run: RUN, search: { th: { merge: 0.9, split: 0.8 } } });
  });
});
