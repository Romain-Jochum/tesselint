// Run diff (docs/ui/DESIGN.md §2 Runs; §4.3 diff_runs): stories added/removed/edited/renamed, proposals, decisions to re-review.
import { useQuery } from "@tanstack/react-query";
import { WordDiff } from "../components/SideBySide.tsx";
import { getDiff } from "../lib/api.ts";
import { href, type Route } from "../lib/router.ts";
import { ATTR, TID, type DiffSection } from "../lib/testids.ts";

export interface RunDiffProps {
  route: Extract<Route, { name: "runDiff" }>;
}

const SECTIONS: { id: DiffSection; label: string; description: string }[] = [
  { id: "added", label: "Stories Added", description: "Stories present in new run but not in old run" },
  { id: "removed", label: "Stories Removed", description: "Stories present in old run but not in new run" },
  { id: "edited", label: "Stories Edited", description: "Stories whose rendered text changed" },
  { id: "renamed", label: "Stories Renamed", description: "Identical text moved to a new story ID" },
  { id: "new", label: "Proposals New", description: "Proposals present only in the new run" },
  { id: "gone", label: "Proposals Gone", description: "Proposals present only in the old run" },
  { id: "changed", label: "Proposals Changed", description: "Proposals with |Δ decisive value| ≥ 0.05 or borderline flips" },
  { id: "decisions_to_review", label: "Decisions to Re-review", description: "Stored decisions naming an edited, removed or renamed story" },
];

export function RunDiff(props: RunDiffProps) {
  const { a, b } = props.route.search;

  const { data: diff, isLoading, error } = useQuery({
    queryKey: ["diff", a, b],
    queryFn: () => getDiff(a!, b!),
    enabled: !!a && !!b,
  });

  if (!a || !b) {
    return (
      <section data-view="runDiff" aria-label="Run diff" className="p-6 max-w-4xl mx-auto space-y-4">
        <h1 className="text-xl font-bold tracking-tight text-text">Run Diff</h1>
        <p className="text-muted text-sm">
          Please select two runs to compare. You can tick two runs on the{" "}
          <a href={href({ name: "runs", search: {} })} className="text-emerald-600 underline">
            Runs page
          </a>
          .
        </p>
      </section>
    );
  }

  if (isLoading) {
    return (
      <section data-view="runDiff" aria-label="Run diff" className="p-6 max-w-5xl mx-auto">
        <p className="text-muted text-sm">Computing differences between {a} and {b}…</p>
      </section>
    );
  }

  if (error || !diff) {
    return (
      <section data-view="runDiff" aria-label="Run diff" className="p-6 max-w-5xl mx-auto space-y-4">
        <h1 className="text-xl font-bold tracking-tight text-text">Run Diff Error</h1>
        <p className="text-red-600 text-sm">
          {error instanceof Error ? error.message : "Failed to load run difference."}
        </p>
      </section>
    );
  }

  return (
    <section data-view="runDiff" aria-label={`Diff ${a} to ${b}`} className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text flex items-center gap-2">
            <span>Diff:</span>
            <span className="font-mono text-base font-semibold">{a}</span>
            <span className="text-muted">→</span>
            <span className="font-mono text-base font-semibold">{b}</span>
          </h1>
          <p className="text-xs text-muted mt-0.5">
            Comparing older run <span className="font-mono">{a}</span> with newer run{" "}
            <span className="font-mono">{b}</span>.
          </p>
        </div>

        <a
          href={href({ name: "runs", search: {} })}
          className="btn btn-outline text-xs px-3 py-1.5 border rounded hover:bg-surface-2"
        >
          ← Back to Runs
        </a>
      </div>

      {/* Diff Sections */}
      <div className="space-y-6">
        {SECTIONS.map((sec) => {
          const items = diff[sec.id] ?? [];
          return (
            <div key={sec.id} className="card bg-surface border border-border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between border-b border-border pb-2">
                <div>
                  <h2 className="text-sm font-semibold text-text">{sec.label}</h2>
                  <p className="text-xs text-muted">{sec.description}</p>
                </div>
                <span className="chip text-xs px-2 py-0.5 rounded font-mono bg-surface-2 text-text tabular-nums">
                  {items.length}
                </span>
              </div>

              <ul data-testid={TID.diffList(sec.id)} className="space-y-2">
                {items.length === 0 ? (
                  <li className="text-xs text-muted italic py-1">None</li>
                ) : sec.id === "edited" ? (
                  (items as { id: string; old_text: string; new_text: string }[]).map((item) => (
                    <li
                      key={item.id}
                      {...{ [ATTR.key]: item.id }}
                      className="p-3 border border-border rounded bg-surface-2 space-y-2 text-xs"
                    >
                      <div className="font-mono font-bold text-text">{item.id}</div>
                      <WordDiff oldText={item.old_text} newText={item.new_text} />
                    </li>
                  ))
                ) : sec.id === "renamed" ? (
                  (items as [string, string][]).map((pair) => {
                    const key = `${pair[0]}>${pair[1]}`;
                    return (
                      <li
                        key={key}
                        {...{ [ATTR.key]: key }}
                        className="p-2.5 border border-border rounded bg-surface-2 flex items-center gap-2 text-xs font-mono"
                      >
                        <span className="font-semibold text-text">{pair[0]}</span>
                        <span className="text-muted">renamed to</span>
                        <span className="font-semibold text-emerald-600">{pair[1]}</span>
                      </li>
                    );
                  })
                ) : (
                  (items as string[]).map((key) => (
                    <li
                      key={key}
                      {...{ [ATTR.key]: key }}
                      className="p-2 border border-border rounded bg-surface-2 flex items-center justify-between text-xs font-mono"
                    >
                      <span className="font-medium text-text">{key}</span>
                    </li>
                  ))
                )}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}
