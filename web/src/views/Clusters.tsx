// Clusters (docs/ui/DESIGN.md §2 Other views, J7): clusters by mean o; ?c=C5 shows members side by side.
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { SideBySide } from "../components/SideBySide.tsx";
import { StoryLink } from "../components/StoryLink.tsx";
import { getRun } from "../lib/api.ts";
import { href, navigate, type Route } from "../lib/router.ts";
import type { S, StoryRow } from "../lib/types.ts";

export interface ClustersProps {
  route: Extract<Route, { name: "clusters" }>;
}

export function Clusters(props: ClustersProps) {
  const runRef = props.route.run;
  const search = props.route.search;
  const selectedClusterId = search.c;

  const { data: bundle } = useQuery({
    queryKey: ["run", runRef],
    queryFn: () => getRun(runRef),
  });

  const storiesMap = useMemo(() => {
    const map = new Map<S, StoryRow>();
    if (bundle) {
      for (const s of bundle.stories) map.set(s.id, s);
    }
    return map;
  }, [bundle]);

  // Clusters sorted by mean_overlap descending
  const sortedClusters = useMemo(() => {
    if (!bundle) return [];
    return [...bundle.clusters].sort((a, b) => b.mean_overlap - a.mean_overlap);
  }, [bundle]);

  const activeCluster = useMemo(() => {
    if (!sortedClusters.length) return null;
    if (selectedClusterId) {
      return sortedClusters.find((c) => c.id === selectedClusterId) ?? sortedClusters[0];
    }
    return sortedClusters[0];
  }, [sortedClusters, selectedClusterId]);

  if (!bundle) {
    return (
      <section data-view="clusters" aria-label="Clusters" className="p-6">
        <p className="text-muted text-sm">Loading clusters…</p>
      </section>
    );
  }

  return (
    <section data-view="clusters" aria-label="Clusters" className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text">Clusters</h1>
          <p className="text-xs text-muted mt-0.5">
            Groups of stories with high pairwise overlap, ranked by mean overlap.
          </p>
        </div>
        <span className="chip text-xs px-2.5 py-1 rounded font-mono bg-surface-2 text-text border border-border">
          {sortedClusters.length} clusters total
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 items-start">
        {/* Clusters List sidebar */}
        <div className="md:col-span-1 card bg-surface border border-border rounded-lg p-3 space-y-2 max-h-[750px] overflow-y-auto">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted px-1">
            Ranked by Mean Overlap
          </h2>
          <div className="space-y-1.5 pt-1">
            {sortedClusters.map((c) => {
              const isSelected = activeCluster?.id === c.id;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => navigate({ ...props.route, search: { ...search, c: c.id } })}
                  className={`w-full text-left p-2.5 rounded border transition-colors flex items-center justify-between text-xs ${
                    isSelected
                      ? "bg-emerald-50 dark:bg-emerald-950/40 border-emerald-500 font-semibold"
                      : "bg-surface-2 border-border hover:border-text-2"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm">{c.id}</span>
                    <span className="text-muted">({c.members.length} stories)</span>
                  </div>
                  <span className="font-mono tabular-nums text-text">
                    o {c.mean_overlap.toFixed(2)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Selected Cluster Details & Side-by-Side members */}
        <div className="md:col-span-3 space-y-4">
          {activeCluster ? (
            <div className="space-y-4">
              <div className="card bg-surface border border-border rounded-lg p-4 flex flex-wrap items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-3">
                    <h2 className="font-mono text-xl font-bold text-text">Cluster {activeCluster.id}</h2>
                    <span className="chip text-xs px-2 py-0.5 rounded font-mono bg-surface-2 text-text border border-border">
                      {activeCluster.members.length} member stories
                    </span>
                    <span className="chip text-xs px-2 py-0.5 rounded font-mono bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800 font-bold">
                      mean o: {activeCluster.mean_overlap.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {activeCluster.members.map((m) => (
                      <StoryLink key={m} run={runRef} id={m} className="text-xs px-2 py-0.5 border rounded bg-surface-2" />
                    ))}
                  </div>
                </div>

                <a
                  href={href({
                    name: "review",
                    run: runRef,
                    search: { kind: "overlap" },
                  })}
                  className="btn btn-outline text-xs px-3 py-1.5 border rounded hover:bg-surface-2"
                >
                  Review Overlaps →
                </a>
              </div>

              {/* Member Stories Side-by-Side or Multi-column */}
              {activeCluster.members.length === 2 &&
              storiesMap.get(activeCluster.members[0]!) &&
              storiesMap.get(activeCluster.members[1]!) ? (
                <div className="card bg-surface border border-border rounded-lg p-4">
                  <SideBySide
                    run={runRef}
                    a={storiesMap.get(activeCluster.members[0]!)!}
                    b={storiesMap.get(activeCluster.members[1]!)!}
                    captions={[`Cluster ${activeCluster.id}`, `mean o ${activeCluster.mean_overlap.toFixed(2)}`]}
                  />
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {activeCluster.members.map((id) => {
                    const story = storiesMap.get(id);
                    if (!story) return null;
                    return (
                      <div
                        key={story.id}
                        className="card bg-surface border border-border rounded-lg p-4 flex flex-col justify-between space-y-3"
                      >
                        <div className="space-y-2">
                          <div className="flex items-center justify-between border-b border-border pb-2">
                            <StoryLink run={runRef} id={story.id} title={story.title} className="text-sm font-semibold" />
                            <span className="chip text-xs px-1.5 py-0.5 rounded font-mono bg-surface-2 text-muted border border-border">
                              {story.epic}
                            </span>
                          </div>
                          <h3 className="text-xs font-semibold text-text-2">{story.title}</h3>
                          <p className="text-xs text-text leading-relaxed whitespace-pre-wrap">{story.text}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
            <div className="card bg-surface border border-border rounded-lg p-6 text-center text-muted text-sm">
              No cluster selected.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
