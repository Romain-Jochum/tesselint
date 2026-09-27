// Matrix (docs/ui/DESIGN.md §2 Matrix, J7 J5; §5 Heatmap): epic and story levels, order, t, Table twin, pair drawer.
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Heatmap, type HeatGroup, type HeatLegend } from "../components/Heatmap.tsx";
import { PairDrawer } from "../components/PairDrawer.tsx";
import { getMatrix, getPair, getRun } from "../lib/api.ts";
import { relationLabel } from "../lib/labels.ts";
import { axisGroups, cellAt, epicAggregate, orderBy, packIndex } from "../lib/matrix.ts";
import { navigate, type Route } from "../lib/router.ts";
import { TID } from "../lib/testids.ts";
import type { N, S, StoryRow } from "../lib/types.ts";

export interface MatrixProps {
  route: Extract<Route, { name: "matrix" }>;
}

export function Matrix(props: MatrixProps) {
  const runRef = props.route.run;
  const search = props.route.search;

  const { data: bundle } = useQuery({
    queryKey: ["run", runRef],
    queryFn: () => getRun(runRef),
  });

  const { data: matrix } = useQuery({
    queryKey: ["matrix", runRef],
    queryFn: () => getMatrix(runRef),
  });

  const level = search.level ?? "epics";
  const order = search.order ?? "doc";
  const t = search.t ?? bundle?.thresholds.cluster ?? 0.7;
  const view = search.view ?? "heatmap";

  // Selected pair for drawer
  const [pairA, pairB] = search.pair ?? [null, null];

  const { data: pairDetail } = useQuery({
    queryKey: ["pair", runRef, pairA, pairB],
    queryFn: () => getPair(runRef, pairA!, pairB!),
    enabled: !!pairA && !!pairB,
  });

  const stories = bundle?.stories ?? [];
  const storiesMap = useMemo(() => {
    const map = new Map<S, StoryRow>();
    for (const s of stories) map.set(s.id, s);
    return map;
  }, [stories]);

  const epics = useMemo(() => bundle?.epics.map((e) => e.id) ?? [], [bundle]);

  // Epic grid aggregate
  const epicGrid = useMemo(() => {
    if (!matrix || !bundle) return null;
    return epicAggregate(matrix, bundle.stories, epics, t);
  }, [matrix, bundle, epics, t]);

  // Story ordering
  const orderedIndices = useMemo(() => {
    if (!bundle) return [];
    return orderBy(bundle.stories, order, bundle.clusters);
  }, [bundle, order]);

  // Drilldown subsets if rows/cols specify epic IDs
  const isDrilldown = level === "stories" && !!search.rows && !!search.cols;

  const displayRows = useMemo(() => {
    if (level === "epics") return epics;
    if (isDrilldown) {
      return stories.filter((s) => s.epic === search.rows).map((s) => s.id);
    }
    return orderedIndices.map((i) => stories[i]?.id ?? "");
  }, [level, isDrilldown, epics, stories, search.rows, orderedIndices]);

  const displayCols = useMemo(() => {
    if (level === "epics") return epics;
    if (isDrilldown) {
      return stories.filter((s) => s.epic === search.cols).map((s) => s.id);
    }
    return orderedIndices.map((i) => stories[i]?.id ?? "");
  }, [level, isDrilldown, epics, stories, search.cols, orderedIndices]);

  const idIndex = useMemo(() => new Map((matrix?.ids ?? []).map((id, i) => [id, i])), [matrix]);
  const cellIndex = useMemo(() => new Map((epicGrid?.cells ?? []).map((cell) => [`${cell.row}|${cell.col}`, cell])), [epicGrid]);
  // Epic level colours the share of pairs ≥ t on [0, the largest share], so small shares stay visible.
  const maxShare = useMemo(() => Math.max(0, ...(epicGrid?.cells ?? []).map((cell) => cell.share)), [epicGrid]);
  const titleOf = (id: S) => storiesMap.get(id)?.title ?? "";

  // Value getter
  const getValue = (r: N, c: N): N | null => {
    if (level === "epics") {
      const cell = cellIndex.get(`${epics[r]}|${epics[c]}`);
      if (!cell || cell.pairs === 0) return null;
      return maxShare > 0 ? cell.share / maxShare : 0;
    }
    if (!matrix) return null;
    const rowId = displayRows[r];
    const colId = displayCols[c];
    if (!rowId || !colId || rowId === colId) return null;
    const idxA = idIndex.get(rowId);
    const idxB = idIndex.get(colId);
    if (idxA === undefined || idxB === undefined) return null;
    return matrix.o[packIndex(idxA, idxB, matrix.ids.length)] ?? null;
  };

  // Tooltip generator
  const getTooltip = (r: N, c: N): S => {
    if (level === "epics") {
      const cell = cellIndex.get(`${epics[r]}|${epics[c]}`);
      if (!cell) return "";
      const top = cell.top
        ? ` · top ${cell.top.a} ${cell.top.relation?.includes("contains") ? "⊃" : "~"} ${cell.top.b} ${cell.top.o.toFixed(2)}`
        : "";
      const mean = cell.mean_o === null ? "" : ` · mean o ${cell.mean_o.toFixed(2)}`;
      return `${cell.row} × ${cell.col} · ${cell.above} of ${cell.pairs} pairs ≥ ${t.toFixed(2)}${mean}${top}\nClick to open these stories`;
    }
    const rowId = displayRows[r] ?? "";
    const colId = displayCols[c] ?? "";
    if (rowId === colId) return `${rowId} (diagonal)\n${titleOf(rowId)}`;
    const idxA = idIndex.get(rowId);
    const idxB = idIndex.get(colId);
    if (!matrix || idxA === undefined || idxB === undefined) return `${rowId} × ${colId}`;
    const cell = cellAt(matrix, idxA, idxB);
    const relation = cell.relation ? ` · ${relationLabel(cell.relation, rowId, colId)}` : "";
    return `${rowId} × ${colId} · o ${cell.o?.toFixed(2) ?? "–"} · m ${cell.m?.toFixed(2) ?? "–"}${relation}\n${rowId} ${titleOf(rowId)}\n${colId} ${titleOf(colId)}`;
  };

  // Axis groups: every epic at epic level; epic runs (document order, drill-down) or clusters at story level.
  const epicNames = useMemo(() => new Map((bundle?.epics ?? []).map((e) => [e.id, e.name])), [bundle]);
  const clusterOf = useMemo(() => {
    const map = new Map<S, S>();
    for (const cl of bundle?.clusters ?? []) for (const id of cl.members) if (!map.has(id)) map.set(id, cl.id);
    return map;
  }, [bundle]);
  const groupsOf = (labels: S[]): HeatGroup[] => {
    if (level === "epics") return labels.map((id, i) => ({ start: i, count: 1, label: id, title: epicNames.get(id) }));
    const byCluster = order === "cluster" && !isDrilldown;
    const keyOf = (id: S) => (byCluster ? (clusterOf.get(id) ?? null) : (storiesMap.get(id)?.epic ?? null));
    return axisGroups(labels, keyOf).map((g) => ({
      start: g.start,
      count: g.count,
      label: g.key,
      title: g.key ? (byCluster ? `Cluster ${g.key}` : `${g.key} ${epicNames.get(g.key) ?? ""}`) : "Not in a cluster",
    }));
  };

  const legend: HeatLegend =
    level === "epics"
      ? {
          caption: `Share of an epic pair's story pairs with o ≥ ${t.toFixed(2)}`,
          ticks: [
            [0, "0%"],
            [1, `${Math.round(maxShare * 100)}%`],
          ],
        }
      : {
          caption: "o = probability of overlap",
          ticks: [
            [0, "0"],
            [0.5, "0.5"],
            [1, "1"],
          ],
          threshold: { at: t, label: `t ${t.toFixed(2)}` },
        };

  const handleActivate = (cell: { row: N; col: N }) => {
    if (level === "epics") {
      const rowEpic = epics[cell.row]!;
      const colEpic = epics[cell.col]!;
      navigate({
        ...props.route,
        search: { ...search, level: "stories", rows: rowEpic, cols: colEpic },
      });
    } else {
      const rowId = displayRows[cell.row]!;
      const colId = displayCols[cell.col]!;
      if (rowId && colId && rowId !== colId) {
        navigate({
          ...props.route,
          search: { ...search, pair: [rowId, colId] },
        });
      }
    }
  };

  const handleCloseDrawer = () => {
    navigate({
      ...props.route,
      search: { ...search, pair: undefined },
    });
  };

  // Pairs >= t for table view
  const tablePairs = useMemo(() => {
    if (view !== "table" || !matrix) return [];
    const list: { a: S; b: S; o: N; m: N | null }[] = [];
    const n = matrix.ids.length;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const k = packIndex(i, j, n);
        const o = matrix.o[k];
        if (o !== null && o !== undefined && o >= t) {
          list.push({ a: matrix.ids[i]!, b: matrix.ids[j]!, o, m: matrix.m[k] ?? null });
        }
      }
    }
    list.sort((x, y) => y.o - x.o);
    return list;
  }, [view, matrix, t]);

  if (!bundle || !matrix) {
    return (
      <section data-view="matrix" aria-label="Matrix" className="p-6">
        <p className="text-muted text-sm">Loading matrix data…</p>
      </section>
    );
  }

  return (
    <section data-view="matrix" aria-label="Matrix" className="p-4 flex flex-col flex-1 min-h-0 space-y-4">
      {/* Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-3 bg-surface border border-border rounded-lg text-xs">
        <div className="flex items-center gap-2">
          <span className="text-muted font-medium">Level:</span>
          <button
            type="button"
            data-testid={TID.level("epics")}
            onClick={() => navigate({ ...props.route, search: { ...search, level: "epics", rows: undefined, cols: undefined } })}
            className={`px-2.5 py-1 border rounded font-medium ${
              level === "epics" ? "bg-emerald-700 text-white border-emerald-800 dark:border-emerald-600" : "bg-surface-2 text-text"
            }`}
          >
            Epics
          </button>
          <button
            type="button"
            data-testid={TID.level("stories")}
            onClick={() => navigate({ ...props.route, search: { ...search, level: "stories" } })}
            className={`px-2.5 py-1 border rounded font-medium ${
              level === "stories" ? "bg-emerald-700 text-white border-emerald-800 dark:border-emerald-600" : "bg-surface-2 text-text"
            }`}
          >
            Stories
          </button>
        </div>

        {level === "stories" && (
          <div className="flex items-center gap-2">
            <span className="text-muted font-medium">Order:</span>
            <button
              type="button"
              data-testid={TID.order("doc")}
              onClick={() => navigate({ ...props.route, search: { ...search, order: "doc" } })}
              className={`px-2.5 py-1 border rounded font-medium ${
                order === "doc" ? "bg-emerald-700 text-white border-emerald-800 dark:border-emerald-600" : "bg-surface-2 text-text"
              }`}
            >
              Document
            </button>
            <button
              type="button"
              data-testid={TID.order("cluster")}
              onClick={() => navigate({ ...props.route, search: { ...search, order: "cluster" } })}
              className={`px-2.5 py-1 border rounded font-medium ${
                order === "cluster" ? "bg-emerald-700 text-white border-emerald-800 dark:border-emerald-600" : "bg-surface-2 text-text"
              }`}
            >
              Cluster
            </button>
          </div>
        )}

        <div className="flex items-center gap-2">
          <label htmlFor="t-slider" className="text-muted font-medium">
            t: <span className="font-mono text-text tabular-nums">{t.toFixed(2)}</span>
          </label>
          <input
            id="t-slider"
            type="range"
            data-testid={TID.tSlider}
            min="0"
            max="1"
            step="0.01"
            value={t}
            onChange={(e) => navigate({ ...props.route, search: { ...search, t: Number(e.target.value) } })}
            className="w-32"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="text-muted font-medium">View:</span>
          <button
            type="button"
            onClick={() => navigate({ ...props.route, search: { ...search, view: "heatmap" } })}
            className={`px-2.5 py-1 border rounded font-medium ${
              view === "heatmap" ? "bg-emerald-700 text-white border-emerald-800 dark:border-emerald-600" : "bg-surface-2 text-text"
            }`}
          >
            Heatmap
          </button>
          <button
            type="button"
            onClick={() => navigate({ ...props.route, search: { ...search, view: "table" } })}
            className={`px-2.5 py-1 border rounded font-medium ${
              view === "table" ? "bg-emerald-700 text-white border-emerald-800 dark:border-emerald-600" : "bg-surface-2 text-text"
            }`}
          >
            Table
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      {view === "table" ? (
        <div className="flex-1 min-h-0 overflow-auto bg-surface rounded-lg border border-border p-4">
          <table data-testid={TID.matrixTable} className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="py-2 px-3">Story A</th>
                <th className="py-2 px-3">Story B</th>
                <th className="py-2 px-3">Overlap (o)</th>
                <th className="py-2 px-3">Merge (m)</th>
              </tr>
            </thead>
            <tbody>
              {tablePairs.map((p) => (
                <tr
                  key={`${p.a}~${p.b}`}
                  onClick={() => navigate({ ...props.route, search: { ...search, pair: [p.a, p.b] } })}
                  className="border-b border-border/50 hover:bg-surface-2 cursor-pointer font-mono"
                >
                  <td className="py-2 px-3 font-semibold">{p.a}</td>
                  <td className="py-2 px-3 font-semibold">{p.b}</td>
                  <td className="py-2 px-3 tabular-nums">{p.o.toFixed(2)}</td>
                  <td className="py-2 px-3 tabular-nums">{p.m?.toFixed(2) ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex-1 min-h-0 flex items-center justify-center overflow-auto bg-surface rounded-lg border border-border p-4">
          <Heatmap
            rows={displayRows}
            cols={displayCols}
            value={getValue}
            t={level === "epics" ? 0 : t}
            diagonal={level === "stories" && (!isDrilldown || search.rows === search.cols)}
            rowGroups={groupsOf(displayRows)}
            colGroups={groupsOf(displayCols)}
            legend={legend}
            tooltip={getTooltip}
            label="Matrix heatmap"
            onActivate={handleActivate}
          />
        </div>
      )}

      {/* Pair Drawer */}
      {pairDetail && pairA && pairB && storiesMap.get(pairA) && storiesMap.get(pairB) && (
        <PairDrawer
          run={runRef}
          pair={pairDetail}
          a={storiesMap.get(pairA)!}
          b={storiesMap.get(pairB)!}
          onClose={handleCloseDrawer}
        />
      )}
    </section>
  );
}
