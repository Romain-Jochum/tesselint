// In-house History API router (docs/ui/DESIGN.md §5 "Routing and state"; routes and URL params §2).
// parseLocation and href must round-trip: parseLocation(urlOf(href(r))) deep-equals r. Absent
// search params stay undefined (never defaulted here); views apply the defaults in DEFAULTS.

import { useSyncExternalStore } from "react";
import type { Kind, S, N, ThOverrides } from "./types.ts";

/** Tabs under /r/:run (§2 chrome). */
export type RunTab = "overview" | "review" | "matrix" | "clusters" | "export";

/** Review `status` filter (§2 Review URL); `open` = undecided + stale. */
export type StatusFilter = "open" | "stale" | "decided" | "all" | "orphaned";

/** Review URL params (§2): case, opt, kind, epic, min, bl, status, preset, sort, th. */
export interface ReviewSearch {
  case?: S;
  opt?: N;
  kind?: Kind;
  epic?: S;
  min?: N;
  bl?: "all" | "only" | "hide";
  status?: StatusFilter;
  preset?: "o70";
  sort?: "strength" | "doc";
  th?: ThOverrides;
}

/** Matrix URL params (§2): level, order, t, rows, cols, pair (`US-1.1~US-20.2`), view. */
export interface MatrixSearch {
  level?: "epics" | "stories";
  order?: "doc" | "cluster";
  t?: N;
  rows?: S;
  cols?: S;
  pair?: [S, S];
  view?: "heatmap" | "table";
}

/** Clusters URL params (§2): `?c=C5` shows that cluster's members side by side. */
export interface ClustersSearch {
  c?: S;
}

/** Export URL params (§2): stale items only with include_stale=1. */
export interface ExportSearch {
  include_stale?: boolean;
}

/** Runs URL params (§2): dry runs only with include=dry. */
export interface RunsSearch {
  include?: "dry";
}

/** Run diff URL params (§2): /runs/diff?a=&b= (refs). */
export interface RunDiffSearch {
  a?: S;
  b?: S;
}

/** Re-run URL params (§2): /runs/new?source=0&tier=t3 or ?resume=<ref>. */
export interface ReRunSearch {
  source?: N;
  tier?: S;
  resume?: S;
}

/** Every route of §2, discriminated by `name`. */
export type Route =
  | { name: "home" } // `/`: opens the latest run
  | { name: "overview"; run: S }
  | { name: "review"; run: S; search: ReviewSearch }
  | { name: "matrix"; run: S; search: MatrixSearch }
  | { name: "clusters"; run: S; search: ClustersSearch }
  | { name: "export"; run: S; search: ExportSearch }
  | { name: "story"; run: S; id: S }
  | { name: "epic"; run: S; id: S }
  | { name: "runs"; search: RunsSearch }
  | { name: "runDiff"; search: RunDiffSearch }
  | { name: "reRun"; search: ReRunSearch }
  | { name: "job"; id: S }
  | { name: "notFound"; path: S };

export type RouteName = Route["name"];

/** Defaults the DESIGN states (§2): Review status `open`; Matrix level `epics`. Data, not parsing. */
export const DEFAULTS = { status: "open", level: "epics" } as const satisfies {
  status: StatusFilter;
  level: MatrixSearch["level"];
};

// ---- param readers: each returns undefined for an absent or bad value ----

type Params = URLSearchParams;

function str(p: Params, name: S): S | undefined {
  const value = p.get(name);
  return value === null || value === "" ? undefined : value;
}

function oneOf<T extends S>(p: Params, name: S, allowed: readonly T[]): T | undefined {
  const value = p.get(name);
  return allowed.find((a) => a === value);
}

function num(p: Params, name: S, ok: (n: N) => boolean): N | undefined {
  const value = str(p, name);
  if (value === undefined || value.trim() === "") return undefined;
  const n = Number(value);
  return Number.isFinite(n) && ok(n) ? n : undefined;
}

const unit = (n: N) => n >= 0 && n <= 1;
const KINDS = ["merge", "overlap", "split"] as const;
const STATUSES = ["open", "stale", "decided", "all", "orphaned"] as const;
const TABS = ["overview", "review", "matrix", "clusters", "export"] as const;

/** Drop undefined fields so absent params never appear as keys. */
function compact<T extends object>(obj: T): T {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined)) as T;
}

function parsePair(p: Params): [S, S] | undefined {
  const parts = str(p, "pair")?.split("~");
  return parts?.length === 2 && parts[0] && parts[1] ? [parts[0], parts[1]] : undefined;
}

function reviewSearch(p: Params): ReviewSearch {
  const th = str(p, "th");
  return compact({
    case: str(p, "case"),
    opt: num(p, "opt", (n) => Number.isInteger(n) && n >= 1),
    kind: oneOf(p, "kind", KINDS),
    epic: str(p, "epic"),
    min: num(p, "min", unit),
    bl: oneOf(p, "bl", ["all", "only", "hide"] as const),
    status: oneOf(p, "status", STATUSES),
    preset: oneOf(p, "preset", ["o70"] as const),
    sort: oneOf(p, "sort", ["strength", "doc"] as const),
    th: th === undefined ? undefined : (parseTh(th) ?? undefined),
  });
}

function matrixSearch(p: Params): MatrixSearch {
  return compact({
    level: oneOf(p, "level", ["epics", "stories"] as const),
    order: oneOf(p, "order", ["doc", "cluster"] as const),
    t: num(p, "t", unit),
    rows: str(p, "rows"),
    cols: str(p, "cols"),
    pair: parsePair(p),
    view: oneOf(p, "view", ["heatmap", "table"] as const),
  });
}

function runRoute(run: S, rest: S[], p: Params): Route | null {
  const [first, id, ...extra] = rest;
  if (extra.length > 0) return null;
  if ((first === "story" || first === "epic") && id) return { name: first, run, id };
  if (id !== undefined) return null;
  const tab = TABS.find((t) => t === first);
  switch (tab) {
    case "overview":
      return { name: "overview", run };
    case "review":
      return { name: "review", run, search: reviewSearch(p) };
    case "matrix":
      return { name: "matrix", run, search: matrixSearch(p) };
    case "clusters":
      return { name: "clusters", run, search: compact({ c: str(p, "c") }) };
    case "export": {
      const stale = p.get("include_stale");
      const include_stale = stale === "1" ? true : stale === "0" ? false : undefined;
      return { name: "export", run, search: compact({ include_stale }) };
    }
    default:
      return null;
  }
}

function runsRoute(rest: S[], p: Params): Route | null {
  const [first, id, ...extra] = rest;
  if (extra.length > 0) return null;
  if (first === undefined) return { name: "runs", search: compact({ include: oneOf(p, "include", ["dry"] as const) }) };
  if (first === "job" && id) return { name: "job", id };
  if (id !== undefined) return null;
  if (first === "diff") return { name: "runDiff", search: compact({ a: str(p, "a"), b: str(p, "b") }) };
  if (first === "new") {
    const search: ReRunSearch = {
      source: num(p, "source", (n) => Number.isInteger(n) && n >= 0),
      tier: str(p, "tier"),
      resume: str(p, "resume"),
    };
    return { name: "reRun", search: compact(search) };
  }
  return null;
}

/** Map a location to a Route (§5). Unknown paths give `notFound`; bad param values are dropped. */
export function parseLocation(loc: { pathname: S; search: S }): Route {
  const notFound: Route = { name: "notFound", path: loc.pathname };
  let segments: S[];
  try {
    segments = loc.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    return notFound;
  }
  const params = new URLSearchParams(loc.search);
  const [head, ...rest] = segments;
  let route: Route | null = null;
  if (head === undefined) route = { name: "home" };
  else if (head === "r" && rest[0]) route = runRoute(rest[0], rest.slice(1), params);
  else if (head === "runs") route = runsRoute(rest, params);
  return route ?? notFound;
}

// ---- href ----

/** encodeURIComponent, keeping `:` and `,` readable (both legal in a query); `+` stays %2B. */
function encodeParam(value: S): S {
  return encodeURIComponent(value).replace(/%3A/gi, ":").replace(/%2C/gi, ",");
}

type ParamValue = S | N | boolean | undefined;

function query(entries: [S, ParamValue][]): S {
  const parts = entries
    .filter((entry): entry is [S, S | N | boolean] => entry[1] !== undefined && entry[1] !== "")
    .map(([k, v]) => `${k}=${encodeParam(typeof v === "boolean" ? (v ? "1" : "0") : String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

const seg = (value: S) => encodeURIComponent(value);

/** Map a Route to `pathname + search` (§5); the inverse of parseLocation. */
export function href(route: Route): S {
  switch (route.name) {
    case "home":
      return "/";
    case "overview":
      return `/r/${seg(route.run)}/overview`;
    case "review": {
      const s = route.search;
      const th = s.th ? formatTh(s.th) : undefined;
      return `/r/${seg(route.run)}/review${query([
        ["case", s.case],
        ["opt", s.opt],
        ["kind", s.kind],
        ["epic", s.epic],
        ["min", s.min],
        ["bl", s.bl],
        ["status", s.status],
        ["preset", s.preset],
        ["sort", s.sort],
        ["th", th],
      ])}`;
    }
    case "matrix": {
      const s = route.search;
      return `/r/${seg(route.run)}/matrix${query([
        ["level", s.level],
        ["order", s.order],
        ["t", s.t],
        ["rows", s.rows],
        ["cols", s.cols],
        ["pair", s.pair ? `${s.pair[0]}~${s.pair[1]}` : undefined],
        ["view", s.view],
      ])}`;
    }
    case "clusters":
      return `/r/${seg(route.run)}/clusters${query([["c", route.search.c]])}`;
    case "export":
      return `/r/${seg(route.run)}/export${query([["include_stale", route.search.include_stale]])}`;
    case "story":
    case "epic":
      return `/r/${seg(route.run)}/${route.name}/${seg(route.id)}`;
    case "runs":
      return `/runs${query([["include", route.search.include]])}`;
    case "runDiff":
      return `/runs/diff${query([
        ["a", route.search.a],
        ["b", route.search.b],
      ])}`;
    case "reRun":
      return `/runs/new${query([
        ["source", route.search.source],
        ["tier", route.search.tier],
        ["resume", route.search.resume],
      ])}`;
    case "job":
      return `/runs/job/${seg(route.id)}`;
    case "notFound":
      return route.path;
  }
}

// ---- th param ----

const TH_FIELDS = [
  ["m", "merge"],
  ["c", "cluster"],
  ["s", "split"],
] as const;

/** Parse the Review `th` param, e.g. "m0.5,c0.7,s0.8" (§2); null when malformed. */
export function parseTh(value: S): ThOverrides | null {
  const th: ThOverrides = {};
  for (const part of value.split(",")) {
    const match = /^([mcs])(\d+(?:\.\d+)?|\.\d+)$/.exec(part);
    const field = TH_FIELDS.find(([letter]) => letter === match?.[1])?.[1];
    const n = Number(match?.[2]);
    if (!field || th[field] !== undefined || !unit(n)) return null;
    th[field] = n;
  }
  return th;
}

/** Format threshold overrides as the `th` param (§2), in m, c, s order. */
export function formatTh(th: ThOverrides): S {
  return TH_FIELDS.filter(([, field]) => th[field] !== undefined)
    .map(([letter, field]) => `${letter}${String(th[field])}`)
    .join(",");
}

// ---- history store and hook ----

const listeners = new Set<() => void>();
const HOME: Route = { name: "home" };
let cached: { key: S; route: Route } | null = null;

function snapshot(): Route {
  const { pathname, search } = window.location;
  const key = pathname + search;
  if (cached?.key !== key) cached = { key, route: parseLocation({ pathname, search }) };
  return cached.route;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

/** Push (or replace) a route onto history and notify useRoute subscribers (§5). */
export function navigate(route: Route, options?: { replace?: boolean }): void {
  const url = href(route);
  if (options?.replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
  for (const listener of [...listeners]) listener();
}

/** The current Route, re-rendering on navigate and popstate (§5). */
export function useRoute(): Route {
  return useSyncExternalStore(subscribe, snapshot, () => HOME);
}

/** The minimal click fields a link handler needs (React's MouseEvent satisfies it). */
export interface LinkClick {
  button: N;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  defaultPrevented: boolean;
  preventDefault(): void;
}

/**
 * onClick for an `<a href={href(route)}>`: a plain left click navigates in-app; modified clicks
 * (new tab, new window, download) keep the browser's default.
 */
export function followLink(event: LinkClick, route: Route, options?: { replace?: boolean }): void {
  if (event.defaultPrevented || event.button !== 0) return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(route, options);
}
