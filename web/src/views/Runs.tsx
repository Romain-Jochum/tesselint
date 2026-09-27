// Runs (docs/ui/DESIGN.md §2 Other views, J6): ref, kind, source, scope, status, cost, staleness; tick two to diff.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getRuns } from "../lib/api.ts";
import { href, navigate, type Route } from "../lib/router.ts";
import { ATTR, TID } from "../lib/testids.ts";
import type { S } from "../lib/types.ts";

export interface RunsProps {
  route: Extract<Route, { name: "runs" }>;
}

export function Runs(props: RunsProps) {
  const search = props.route.search;
  const includeDry = search.include === "dry";
  const [ticked, setTicked] = useState<S[]>([]);

  const { data: runsData } = useQuery({
    queryKey: ["runs", includeDry],
    queryFn: () => getRuns({ includeDry }),
  });

  const runs = runsData?.runs ?? [];

  const handleTick = (ref: S, checked: boolean) => {
    let next: S[];
    if (checked) {
      next = [...ticked, ref];
    } else {
      next = ticked.filter((r) => r !== ref);
    }
    setTicked(next);

    if (next.length === 2) {
      // Order older (a) -> newer (b)
      // runs list is sorted newest first: higher index is older
      const [r1, r2] = next;
      const idx1 = runs.findIndex((r) => r.ref === r1);
      const idx2 = runs.findIndex((r) => r.ref === r2);
      const a = idx1 > idx2 ? r1 : r2;
      const b = idx1 > idx2 ? r2 : r1;
      setTimeout(() => {
        navigate({ name: "runDiff", search: { a, b } });
      }, 50);
    }
  };

  return (
    <section data-view="runs" aria-label="Runs" className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text">Runs</h1>
          <p className="text-xs text-muted mt-0.5">
            History of pipeline runs. Tick any two runs to compare them.
          </p>
        </div>

        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-xs text-text-2 cursor-pointer">
            <input
              type="checkbox"
              checked={includeDry}
              onChange={(e) =>
                navigate({
                  name: "runs",
                  search: { include: e.target.checked ? "dry" : undefined },
                })
              }
              className="rounded border-border text-emerald-600 focus:ring-emerald-500"
            />
            <span>Include dry runs</span>
          </label>

          <a
            href={href({ name: "reRun", search: {} })}
            className="btn btn-primary text-xs px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded font-medium shadow-sm transition-colors"
          >
            + New Run
          </a>
        </div>
      </div>

      <div className="card bg-surface border border-border rounded-lg overflow-hidden">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-border bg-surface-2/60 text-muted font-medium">
              <th className="py-2.5 px-3 w-10 text-center">Diff</th>
              <th className="py-2.5 px-3">Run Ref</th>
              <th className="py-2.5 px-3">Kind</th>
              <th className="py-2.5 px-3">Status</th>
              <th className="py-2.5 px-3">Stories</th>
              <th className="py-2.5 px-3">Cost</th>
              <th className="py-2.5 px-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-8 text-center text-muted">
                  No runs found.
                </td>
              </tr>
            ) : (
              runs.map((r) => {
                const isTicked = ticked.includes(r.ref);
                return (
                  <tr
                    key={r.ref}
                    data-testid={TID.runRow}
                    {...{ [ATTR.key]: r.ref }}
                    className={`border-b border-border/60 hover:bg-surface-2 transition-colors ${
                      isTicked ? "bg-emerald-50/40 dark:bg-emerald-950/20" : ""
                    }`}
                  >
                    <td className="py-2 px-3 text-center">
                      <input
                        type="checkbox"
                        data-testid={TID.runTick}
                        {...{ [ATTR.key]: r.ref }}
                        checked={isTicked}
                        onChange={(e) => handleTick(r.ref, e.target.checked)}
                        className="rounded border-border text-emerald-600 focus:ring-emerald-500 cursor-pointer"
                        title="Tick two runs to diff"
                      />
                    </td>
                    <td className="py-2 px-3 font-mono font-medium">
                      <a
                        href={href({ name: "overview", run: r.ref })}
                        className="text-text hover:text-emerald-600 transition-colors"
                      >
                        {r.ref}
                      </a>
                    </td>
                    <td className="py-2 px-3">
                      <span
                        className={`chip text-xs px-1.5 py-0.5 rounded font-mono ${
                          r.kind === "real"
                            ? "bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-200"
                            : "bg-surface-2 text-muted"
                        }`}
                      >
                        {r.kind}
                        {r.dry_run ? " (dry)" : ""}
                      </span>
                    </td>
                    <td className="py-2 px-3">
                      <span
                        className={`chip text-xs px-1.5 py-0.5 rounded font-medium ${
                          r.status === "complete"
                            ? "text-emerald-700 dark:text-emerald-300"
                            : r.status === "running"
                              ? "text-blue-600"
                              : "text-amber-600"
                        }`}
                      >
                        {r.status}
                      </span>
                    </td>
                    <td className="py-2 px-3 tabular-nums text-text-2">{r.n_stories}</td>
                    <td className="py-2 px-3 font-mono tabular-nums text-text-2">
                      US${(r.cost_live_usd + r.cost_cached_usd).toFixed(2)}
                    </td>
                    <td className="py-2 px-3 text-right">
                      <a
                        href={href({ name: "overview", run: r.ref })}
                        className="text-xs text-emerald-600 hover:underline font-medium"
                      >
                        Open →
                      </a>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
