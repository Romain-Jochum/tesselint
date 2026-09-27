// Story detail (docs/ui/DESIGN.md §2 Other views, J4): fields, source status, neighbours by o, cluster, cases, history.
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { StoryLink } from "../components/StoryLink.tsx";
import { getDecisions, getMatrix, getProposals, getRun } from "../lib/api.ts";
import { packIndex } from "../lib/matrix.ts";
import { href, type Route } from "../lib/router.ts";
import { ATTR, TID } from "../lib/testids.ts";
import type { N, S } from "../lib/types.ts";

export interface StoryProps {
  route: Extract<Route, { name: "story" }>;
}

export function Story(props: StoryProps) {
  const runRef = props.route.run;
  const storyId = props.route.id;

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

  const { data: decisionsData } = useQuery({
    queryKey: ["decisions", runRef],
    queryFn: () => getDecisions(runRef),
  });

  const story = useMemo(() => {
    return bundle?.stories.find((s) => s.id === storyId);
  }, [bundle, storyId]);

  // Neighbours: ranked by o (top 20, o >= 0.4), tie-broken by document order
  const neighbours = useMemo(() => {
    if (!matrix || !bundle) return [];
    const idxA = matrix.ids.indexOf(storyId);
    if (idxA === -1) return [];
    const n = matrix.ids.length;
    const storiesOrderMap = new Map<S, N>();
    bundle.stories.forEach((s, idx) => storiesOrderMap.set(s.id, idx));

    const list: { id: S; o: N; order: N }[] = [];
    for (let idxB = 0; idxB < n; idxB++) {
      if (idxA === idxB) continue;
      const otherId = matrix.ids[idxB]!;
      const k = packIndex(idxA, idxB, n);
      const o = matrix.o[k];
      if (o !== null && o !== undefined && o >= 0.4) {
        list.push({ id: otherId, o, order: storiesOrderMap.get(otherId) ?? idxB });
      }
    }
    // Sort descending by o, tie-break ascending by document order
    list.sort((a, b) => b.o - a.o || a.order - b.order);
    return list.slice(0, 20);
  }, [matrix, bundle, storyId]);

  // Cluster membership
  const cluster = useMemo(() => {
    if (!bundle) return null;
    return bundle.clusters.find((c) => c.members.includes(storyId));
  }, [bundle, storyId]);

  // Related cases
  const relatedCases = useMemo(() => {
    if (!proposalsData) return [];
    return proposalsData.cases.filter((c) =>
      c.anchor === storyId || c.proposals.some((pk) => pk.includes(storyId))
    );
  }, [proposalsData, storyId]);

  // Related decisions
  const relatedDecisions = useMemo(() => {
    if (!decisionsData) return [];
    return decisionsData.decisions.filter(
      (d) => d.target === storyId || d.stories.includes(storyId)
    );
  }, [decisionsData, storyId]);

  // Source status
  const sourceStatus = useMemo(() => {
    if (!bundle?.source_status) return null;
    if (bundle.source_status.edited.includes(storyId)) return "edited";
    if (bundle.source_status.added.includes(storyId)) return "added";
    if (bundle.source_status.removed.includes(storyId)) return "removed";
    return "current";
  }, [bundle, storyId]);

  if (!bundle) {
    return (
      <section data-view="story" aria-label="Story" className="p-6">
        <p className="text-muted text-sm">Loading story details…</p>
      </section>
    );
  }

  if (!story) {
    return (
      <section data-view="story" aria-label="Story" className="p-6">
        <p className="text-muted text-sm">Story {storyId} not found in this run.</p>
      </section>
    );
  }

  return (
    <section data-view="story" aria-label={`Story ${story.id}`} className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-2xl font-bold tracking-tight text-text">{story.id}</h1>
            <a
              href={href({ name: "epic", run: runRef, id: story.epic })}
              className="chip text-xs px-2 py-0.5 rounded font-medium bg-surface-2 text-text hover:border-text-2 border border-border"
            >
              {story.epic}
            </a>
            {story.tag && (
              <span className="chip text-xs px-2 py-0.5 rounded font-mono bg-surface-2 text-muted border border-border">
                {story.tag}
              </span>
            )}
            {sourceStatus && sourceStatus !== "current" && (
              <span className="chip text-xs px-2 py-0.5 rounded font-semibold bg-amber-100 text-amber-900 border border-amber-300">
                source {sourceStatus}
              </span>
            )}
          </div>
          <h2 className="text-lg font-medium text-text-2 mt-1">{story.title}</h2>
        </div>
      </div>

      {/* Story Text / Statement */}
      <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Statement</h3>
        <p className="text-sm text-text leading-relaxed whitespace-pre-wrap">{story.text}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Neighbours by Overlap */}
        <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              Neighbours (o ≥ 0.40)
            </h3>
            <span className="text-xs text-muted tabular-nums">{neighbours.length} found</span>
          </div>

          {neighbours.length === 0 ? (
            <p className="text-xs text-muted">No neighbours with overlap ≥ 0.40</p>
          ) : (
            <ul className="space-y-2">
              {neighbours.map((nb) => {
                const other = bundle.stories.find((s) => s.id === nb.id);
                return (
                  <li
                    key={nb.id}
                    data-testid={TID.neighbour}
                    {...{ [ATTR.key]: nb.id, [ATTR.value]: nb.o }}
                    className="flex items-center justify-between p-2.5 border border-border rounded bg-surface-2 hover:border-text-2 transition-colors"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <StoryLink run={runRef} id={nb.id} title={other?.title} className="text-xs font-semibold" />
                      <span className="text-xs text-muted truncate max-w-xs">{other?.title}</span>
                    </div>
                    <span className="font-mono text-xs tabular-nums text-text font-semibold px-2 py-0.5 bg-surface border border-border rounded">
                      o {nb.o.toFixed(2)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Cluster & Cases */}
        <div className="space-y-6">
          {/* Cluster Membership */}
          <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Cluster</h3>
            {cluster ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <a
                    href={href({ name: "clusters", run: runRef, search: { c: cluster.id } })}
                    className="text-sm font-semibold text-emerald-600 hover:underline"
                  >
                    Cluster {cluster.id}
                  </a>
                  <span className="text-xs text-muted font-mono tabular-nums">
                    mean o: {cluster.mean_overlap.toFixed(2)}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {cluster.members.map((m) => (
                    <StoryLink
                      key={m}
                      run={runRef}
                      id={m}
                      className={`text-xs px-2 py-0.5 border rounded ${
                        m === storyId ? "bg-emerald-50 border-emerald-500 font-bold" : "bg-surface-2"
                      }`}
                    />
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted">Not assigned to any cluster</p>
            )}
          </div>

          {/* Related Review Cases */}
          <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Review Cases</h3>
            {relatedCases.length === 0 ? (
              <p className="text-xs text-muted">No review cases involving this story</p>
            ) : (
              <ul className="space-y-2">
                {relatedCases.map((c) => (
                  <li key={c.key} className="p-2 border border-border rounded bg-surface-2 flex items-center justify-between text-xs">
                    <div>
                      <span className="font-mono font-medium">{c.key}</span>
                      <span className="ml-2 text-muted">({c.kind})</span>
                    </div>
                    <a
                      href={href({ name: "review", run: runRef, search: { case: c.key } })}
                      className="text-emerald-600 hover:underline font-medium"
                    >
                      Open in Review →
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Decision History */}
          {relatedDecisions.length > 0 && (
            <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Decisions</h3>
              <ul className="space-y-2">
                {relatedDecisions.map((d) => (
                  <li key={d.key} className="p-2 border border-border rounded bg-surface-2 space-y-1 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-medium">{d.key}</span>
                      <span className="font-semibold uppercase">{d.action}</span>
                    </div>
                    {d.note && <p className="text-muted italic">{d.note}</p>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
