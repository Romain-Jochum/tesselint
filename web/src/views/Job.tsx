// Job (docs/ui/DESIGN.md §2 Re-run step 4): phase bars, spend vs cap, log tail, Cancel, exit-code messages.
import { useQuery } from "@tanstack/react-query";
import { cancelJob, exitMessage, getJob } from "../lib/api.ts";
import { type Route } from "../lib/router.ts";
import { TID } from "../lib/testids.ts";

export interface JobProps {
  route: Extract<Route, { name: "job" }>;
}

export function Job(props: JobProps) {
  const jobId = props.route.id;

  const { data: job, isLoading, error } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state === "running" ? 1000 : false;
    },
  });

  const handleCancel = async () => {
    try {
      await cancelJob(jobId);
    } catch {
      // handled on next poll
    }
  };

  if (isLoading) {
    return (
      <section data-view="job" aria-label="Job" className="p-6 max-w-4xl mx-auto">
        <p className="text-muted text-sm">Loading job {jobId}…</p>
      </section>
    );
  }

  if (error || !job) {
    return (
      <section data-view="job" aria-label="Job" className="p-6 max-w-4xl mx-auto space-y-4">
        <h1 className="text-xl font-bold tracking-tight text-text">Job Not Found</h1>
        <p className="text-red-600 text-sm">
          {error instanceof Error ? error.message : "Failed to load job."}
        </p>
      </section>
    );
  }

  const message = exitMessage(job);
  const isRunning = job.state === "running";

  return (
    <section data-view="job" aria-label={`Job ${job.id}`} className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold tracking-tight text-text">Job Execution</h1>
            <span
              data-testid={TID.jobState}
              className={`chip text-xs px-2.5 py-0.5 rounded font-mono font-semibold uppercase ${
                job.state === "done"
                  ? "bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-200 border border-emerald-300"
                  : job.state === "failed"
                    ? "bg-red-100 dark:bg-red-950 text-red-800 dark:text-red-200 border border-red-300"
                    : job.state === "cancelled"
                      ? "bg-amber-100 dark:bg-amber-950 text-amber-800 dark:text-amber-200 border border-amber-300"
                      : "bg-blue-100 dark:bg-blue-950 text-blue-800 dark:text-blue-200 border border-blue-300 animate-pulse"
              }`}
            >
              {job.state}
            </span>
          </div>
          <p className="font-mono text-xs text-muted mt-1">{job.id}</p>
        </div>

        <div className="flex items-center gap-3">
          {job.run_ref && (
            <a
              data-testid={TID.jobRunLink}
              href={`/r/${job.run_ref}/overview`}
              className="btn btn-primary text-xs px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded font-medium shadow-sm transition-colors"
            >
              Open Finished Run →
            </a>
          )}
          {isRunning && (
            <button
              type="button"
              data-testid={TID.cancelButton}
              onClick={handleCancel}
              className="btn btn-danger text-xs px-3 py-1.5 border border-red-300 text-red-600 hover:bg-red-50 rounded font-medium"
            >
              Cancel Job
            </button>
          )}
        </div>
      </div>

      {/* Exit message if finished or failed */}
      {message && (
        <div
          data-testid={TID.jobMessage}
          className={`p-3.5 rounded-lg border text-xs font-medium ${
            job.state === "failed"
              ? "bg-red-50 dark:bg-red-950/40 border-red-300 dark:border-red-800 text-red-800 dark:text-red-200"
              : "bg-amber-50 dark:bg-amber-950/40 border-amber-300 dark:border-amber-800 text-amber-800 dark:text-amber-200"
          }`}
        >
          {message}
        </div>
      )}

      {/* Spend vs Cap */}
      <div className="card bg-surface border border-border rounded-lg p-4 flex items-center justify-between text-xs">
        <div>
          <span className="text-muted block">Spend vs Cap</span>
          <span className="font-mono text-base font-bold text-text tabular-nums">
            US${job.spent_usd.toFixed(2)}
          </span>
          <span className="text-muted font-mono tabular-nums"> / US${job.max_usd.toFixed(2)}</span>
        </div>
        <div className="w-1/2 bg-border rounded-full h-2 overflow-hidden">
          <div
            className="bg-emerald-500 h-2 rounded-full transition-all"
            style={{ width: `${Math.min(100, (job.spent_usd / (job.max_usd || 1)) * 100)}%` }}
          />
        </div>
      </div>

      {/* Phase Bars */}
      {job.phases.length > 0 && (
        <div className="card bg-surface border border-border rounded-lg p-4 space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">Phases</h2>
          <div className="space-y-2.5">
            {job.phases.map((phase) => {
              const pct = phase.total ? Math.round((phase.done / phase.total) * 100) : 0;
              return (
                <div key={phase.name} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-mono font-medium text-text">{phase.name}</span>
                    <span className="font-mono tabular-nums text-muted">
                      {phase.done} / {phase.total} ({pct}%)
                    </span>
                  </div>
                  <div className="w-full bg-border rounded-full h-1.5 overflow-hidden">
                    <div
                      className="bg-emerald-500 h-1.5 rounded-full transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Log Tail */}
      <div className="card bg-surface border border-border rounded-lg p-4 space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">Log Tail</h2>
        <pre className="p-3 bg-surface-2 rounded border border-border font-mono text-xs text-text overflow-x-auto max-h-96 whitespace-pre-wrap leading-relaxed">
          {job.log_tail.join("\n") || "No log output yet."}
        </pre>
      </div>
    </section>
  );
}
