import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { canStart, defaultCap, getHealth, postEstimate, startJob } from "../lib/api.ts";
import { navigate, type Route } from "../lib/router.ts";
import { TID } from "../lib/testids.ts";
import type { Estimate, N, S } from "../lib/types.ts";

export interface ReRunProps {
  route: Extract<Route, { name: "reRun" }>;
}

export function ReRun(props: ReRunProps) {
  const search = props.route.search;
  const initialSource = search.source ?? 0;
  const initialTier = search.tier ?? "t3";
  const initialResume = search.resume;

  const [sourceId, setSourceId] = useState<N>(initialSource);
  const [tier, setTier] = useState<S>(initialTier);
  const [resume, setResume] = useState<S | undefined>(initialResume);

  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [cap, setCap] = useState<number>(0);
  const [accepted, setAccepted] = useState(false);
  const [isEstimating, setIsEstimating] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<S | null>(null);

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => getHealth(),
  });

  const handleEstimate = async () => {
    setIsEstimating(true);
    setError(null);
    try {
      const res = await postEstimate({
        source_id: sourceId,
        tier: tier || undefined,
        resume: resume || undefined,
      });
      setEstimate(res);
      const uiMax = health?.ui_max_usd ?? 1.0;
      const initialCap = defaultCap(res.usd, res.key.remaining_usd, uiMax);
      setCap(initialCap);
      setAccepted(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsEstimating(false);
    }
  };

  const handleStart = async () => {
    if (!estimate) return;
    setIsStarting(true);
    setError(null);
    try {
      const job = await startJob({
        estimate_id: estimate.id,
        max_usd: cap,
        confirm: "spend",
      });
      navigate({ name: "job", id: job.id });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setIsStarting(false);
    }
  };

  const uiMax = health?.ui_max_usd ?? 1.0;
  const isStartEnabled =
    !!estimate &&
    !isStarting &&
    canStart({
      estimateUsd: estimate.usd,
      capUsd: cap,
      remainingUsd: estimate.key.remaining_usd,
      uiMaxUsd: uiMax,
      accepted,
    });

  return (
    <section data-view="reRun" aria-label="Re-run" className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="border-b border-border pb-4">
        <h1 className="text-xl font-bold tracking-tight text-text">Re-Run Pipeline</h1>
        <p className="text-xs text-muted mt-0.5">
          Configure and run the pipeline on an input source. A free estimate is calculated before running.
        </p>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-950/40 border border-red-300 dark:border-red-800 rounded-lg text-xs text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {/* Step 1: Input source and scope */}
      <div className="card bg-surface border border-border rounded-lg p-5 space-y-4">
        <h2 className="text-sm font-semibold text-text">1. Select Input Source and Tier</h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-text-2 mb-1">Source File</label>
            <select
              value={sourceId}
              onChange={(e) => setSourceId(Number(e.target.value))}
              className="w-full text-xs font-mono p-2 border border-border rounded bg-surface-2"
            >
              {(health?.sources ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  [{s.id}] {s.path} ({s.format}) {s.exists ? "" : "(missing)"}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-text-2 mb-1">Tier</label>
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value)}
              className="w-full text-xs font-mono p-2 border border-border rounded bg-surface-2"
            >
              <option value="t3">t3 (all pairs, full Jev decision model)</option>
              <option value="t2">t2 (heuristics + model for borderline)</option>
              <option value="t1">t1 (fast embedding similarity only)</option>
            </select>
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-text-2 mb-1">Resume from Run (optional)</label>
          <input
            type="text"
            placeholder="e.g. mini~20260101-090000"
            value={resume ?? ""}
            onChange={(e) => setResume(e.target.value || undefined)}
            className="w-full text-xs font-mono p-2 border border-border rounded bg-surface-2"
          />
        </div>

        <button
          type="button"
          data-testid={TID.estimateButton}
          onClick={handleEstimate}
          disabled={isEstimating}
          className="btn btn-primary text-xs px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded font-medium disabled:opacity-50"
        >
          {isEstimating ? "Estimating…" : "Calculate Free Estimate"}
        </button>
      </div>

      {/* Step 2 & 3: Estimate and Confirmation */}
      {estimate && (
        <div data-testid={TID.estimatePanel} className="card bg-surface border border-border rounded-lg p-5 space-y-4">
          <h2 className="text-sm font-semibold text-text">2. Estimate & Confirmation</h2>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-3 bg-surface-2 rounded-lg text-xs">
            <div>
              <span className="text-muted block">To Ask</span>
              <span className="font-mono text-sm font-bold text-text tabular-nums">{estimate.to_ask}</span>
            </div>
            <div>
              <span className="text-muted block">Cached</span>
              <span className="font-mono text-sm font-bold text-text tabular-nums">{estimate.cached}</span>
            </div>
            <div>
              <span className="text-muted block">Estimated Cost</span>
              <span className="font-mono text-sm font-bold text-emerald-600 tabular-nums">
                US${estimate.usd.toFixed(2)}
              </span>
            </div>
            <div>
              <span className="text-muted block">Key Balance</span>
              <span className="font-mono text-sm font-bold text-text tabular-nums">
                {estimate.key.remaining_usd !== null ? `US$${estimate.key.remaining_usd.toFixed(2)}` : "–"}
              </span>
            </div>
          </div>

          <div>
            <span className="text-xs font-medium text-muted block mb-1">Command</span>
            <div data-testid={TID.estimateCommand} className="p-2.5 bg-surface-2 rounded border border-border font-mono text-xs text-text overflow-x-auto whitespace-pre-wrap">
              {estimate.command}
            </div>
          </div>

          <div className="border-t border-border pt-4 space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Confirm Spend Cap</h3>

            <div className="flex items-center gap-3">
              <label htmlFor="cap-input" className="text-xs font-medium text-text">
                Spend Cap (US$):
              </label>
              <input
                id="cap-input"
                type="number"
                step="0.01"
                min="0"
                data-testid={TID.capInput}
                value={cap}
                onChange={(e) => setCap(Number(e.target.value))}
                className="w-28 p-1.5 border border-border rounded font-mono text-xs tabular-nums bg-surface"
              />
              <span className="text-xs text-muted">
                (Max allowed: US${Math.min(estimate.key.remaining_usd ?? Infinity, uiMax).toFixed(2)})
              </span>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <input
                id="spend-tick"
                type="checkbox"
                data-testid={TID.spendTick}
                checked={accepted}
                onChange={(e) => setAccepted(e.target.checked)}
                className="rounded border-border text-emerald-600 focus:ring-emerald-500 cursor-pointer"
              />
              <label htmlFor="spend-tick" className="text-xs text-text font-medium cursor-pointer">
                I accept spending up to US${cap.toFixed(2)}
              </label>
            </div>

            <button
              type="button"
              data-testid={TID.startButton}
              onClick={handleStart}
              disabled={!isStartEnabled}
              className="btn btn-primary text-xs px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
            >
              {isStarting ? "Starting…" : "Start Run"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
