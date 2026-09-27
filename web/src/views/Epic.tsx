// Epic detail (docs/ui/DESIGN.md §2 Other views, J5): its heatmap, partner epics as ranked bars, its proposals.
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Heatmap } from "../components/Heatmap.tsx";
import { StoryLink } from "../components/StoryLink.tsx";
import { getMatrix, getProposals, getRun } from "../lib/api.ts";
import { cellAt, epicAggregate, packIndex } from "../lib/matrix.ts";
import { href, navigate, type Route } from "../lib/router.ts";
import type { N, S } from "../lib/types.ts";

export interface EpicProps {
  route: Extract<Route, { name: "epic" }>;
}

export function Epic(props: EpicProps) {
  const runRef = props.route.run;
  const epicId = props.route.id;

  const { data: bundle } = useQuery({
    queryKey: ["run", runRef],
    queryFn: () => getRun(runRef),
  });

  const { data: matrix } = useQuery({
    queryKey: ["matrix", runRef],
    queryFn: () => getMatrix(runRef),
  });

  const { data: proposalsData } = useQuery({
    queryKey: ["proposals", runRef],
    queryFn: () => getProposals(runRef),
  });

  const epic = useMemo(() => {
    return bundle?.epics.find((e) => e.id === epicId);
  }, [bundle, epicId]);

  const stories = useMemo(() => {
    return bundle?.stories.filter((s) => s.epic === epicId) ?? [];
  }, [bundle, epicId]);

  const storyIds = useMemo(() => stories.map((s) => s.id), [stories]);

  const allEpics = useMemo(() => bundle?.epics.map((e) => e.id) ?? [], [bundle]);
  const t = bundle?.thresholds.cluster ?? 0.7;

  // Epic grid aggregate for partner epics
  const epicGrid = useMemo(() => {
    if (!matrix || !bundle) return null;
    return epicAggregate(matrix, bundle.stories, allEpics, t);
  }, [matrix, bundle, allEpics, t]);

  // Partner epics ranked by pairs above t or mean o
  const partnerEpics = useMemo(() => {
    if (!epicGrid) return [];
    const myIndex = allEpics.indexOf(epicId);
    if (myIndex === -1) return [];

    const partners = allEpics
      .filter((e) => e !== epicId)
      .map((otherId) => {
        const otherIndex = allEpics.indexOf(otherId);
        const cell = epicGrid.cells[myIndex * allEpics.length + otherIndex];
        return {
          id: otherId,
          pairs: cell?.pairs ?? 0,
          above: cell?.above ?? 0,
          share: cell?.share ?? 0,
          mean_o: cell?.mean_o ?? 0,
        };
      });

    partners.sort((a, b) => b.above - a.above || (b.mean_o ?? 0) - (a.mean_o ?? 0));
    return partners;
  }, [epicGrid, allEpics, epicId]);

  // Sub-matrix value getter
  const getValue = (r: N, c: N): N | null => {
    if (!matrix) return null;
    const rowId = storyIds[r];
    const colId = storyIds[c];
    if (!rowId || !colId || rowId === colId) return null;
    const idxA = matrix.ids.indexOf(rowId);
    const idxB = matrix.ids.indexOf(colId);
    if (idxA === -1 || idxB === -1) return null;
    const k = packIndex(idxA, idxB, matrix.ids.length);
    return matrix.o[k] ?? null;
  };

  const getTooltip = (r: N, c: N): S => {
    const rowId = storyIds[r] ?? "";
    const colId = storyIds[c] ?? "";
    if (rowId === colId) return `${rowId} (diagonal)`;
    if (!matrix) return `${rowId} × ${colId}`;
    const idxA = matrix.ids.indexOf(rowId);
    const idxB = matrix.ids.indexOf(colId);
    if (idxA === -1 || idxB === -1) return `${rowId} × ${colId}`;
    const cell = cellAt(matrix, idxA, idxB);
    return `${rowId} × ${colId} · o: ${cell.o?.toFixed(2) ?? "–"} · m: ${cell.m?.toFixed(2) ?? "–"}`;
  };

  const handleActivateCell = (cell: { row: N; col: N }) => {
    const rowId = storyIds[cell.row];
    const colId = storyIds[cell.col];
    if (rowId && colId && rowId !== colId) {
      navigate({
        name: "matrix",
        run: runRef,
        search: { level: "stories", rows: epicId, cols: epicId, pair: [rowId, colId] },
      });
    }
  };

  // Proposals touching this epic
  const relatedProposals = useMemo(() => {
    if (!proposalsData) return [];
    const storyIdSet = new Set(storyIds);
    return proposalsData.proposals.filter(
      (p) => (p.target && storyIdSet.has(p.target)) || p.stories.some((s) => storyIdSet.has(s))
    );
  }, [proposalsData, storyIds]);

  if (!bundle) {
    return (
      <section data-view="epic" aria-label="Epic" className="p-6">
        <p className="text-muted text-sm">Loading epic details…</p>
      </section>
    );
  }

  if (!epic) {
    return (
      <section data-view="epic" aria-label="Epic" className="p-6">
        <p className="text-muted text-sm">Epic {epicId} not found in this run.</p>
      </section>
    );
  }

  return (
    <section data-view="epic" aria-label={`Epic ${epic.id}`} className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-2xl font-bold tracking-tight text-text">{epic.id}</h1>
            <span className="chip text-xs px-2 py-0.5 rounded font-mono bg-surface-2 text-muted border border-border">
              {stories.length} stories
            </span>
          </div>
          <h2 className="text-lg font-medium text-text-2 mt-1">{epic.name}</h2>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Epic Heatmap */}
        <div className="card bg-surface border border-border rounded-lg p-4 space-y-3 flex flex-col items-center">
          <div className="w-full flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              Within-Epic Heatmap
            </h3>
            <span className="text-xs text-muted font-mono">{stories.length} × {stories.length}</span>
          </div>

          <div className="w-full flex justify-center py-2">
            <Heatmap
              rows={storyIds}
              cols={storyIds}
              value={getValue}
              t={t}
              diagonal={true}
              tooltip={getTooltip}
              label={`Epic ${epic.id} heatmap`}
              onActivate={handleActivateCell}
            />
          </div>
        </div>

        {/* Partner Epics */}
        <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
            Partner Epics (Ranked)
          </h3>
          <p className="text-xs text-muted">Click to drill into the epic-pair block in Matrix view.</p>

          <div className="space-y-2 pt-1 overflow-y-auto max-h-[380px]">
            {partnerEpics.map((p) => {
              const partner = bundle.epics.find((e) => e.id === p.id);
              const maxPairs = Math.max(...partnerEpics.map((x) => x.above), 1);
              const pct = (p.above / maxPairs) * 100;
              return (
                <a
                  key={p.id}
                  href={href({
                    name: "matrix",
                    run: runRef,
                    search: { level: "stories", rows: epicId, cols: p.id },
                  })}
                  className="block p-2.5 border border-border rounded bg-surface-2 hover:border-text-2 transition-colors space-y-1.5"
                >
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-mono font-bold text-text">{p.id}</span>
                    <span className="text-muted truncate max-w-xs">{partner?.name}</span>
                    <span className="font-mono tabular-nums text-text">
                      {p.above} pairs ≥ {t.toFixed(2)}
                    </span>
                  </div>
                  <div className="w-full bg-border rounded-full h-1.5 overflow-hidden">
                    <div
                      className="bg-emerald-500 h-1.5 rounded-full"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </a>
              );
            })}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Stories in Epic */}
        <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
            Stories ({stories.length})
          </h3>
          <ul className="space-y-2 max-h-96 overflow-y-auto">
            {stories.map((s) => (
              <li key={s.id} className="p-2 border border-border rounded bg-surface-2 flex items-center justify-between text-xs">
                <div className="flex items-center gap-2 min-w-0">
                  <StoryLink run={runRef} id={s.id} title={s.title} className="font-semibold" />
                  <span className="text-muted truncate max-w-xs">{s.title}</span>
                </div>
                {s.tag && <span className="font-mono text-muted text-xs">{s.tag}</span>}
              </li>
            ))}
          </ul>
        </div>

        {/* Proposals in Epic */}
        <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
            Proposals Touching {epic.id} ({relatedProposals.length})
          </h3>
          {relatedProposals.length === 0 ? (
            <p className="text-xs text-muted">No proposals touching stories in this epic.</p>
          ) : (
            <ul className="space-y-2 max-h-96 overflow-y-auto">
              {relatedProposals.map((p) => (
                <li key={p.key} className="p-2 border border-border rounded bg-surface-2 flex items-center justify-between text-xs">
                  <div>
                    <span className="font-mono font-medium">{p.key}</span>
                    <span className="ml-2 text-muted">({p.kind})</span>
                  </div>
                  <a
                    href={href({ name: "review", run: runRef, search: { case: p.key } })}
                    className="text-emerald-600 hover:underline font-medium"
                  >
                    Open in Review →
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
