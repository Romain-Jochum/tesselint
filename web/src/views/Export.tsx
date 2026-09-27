// Export (docs/ui/DESIGN.md §2 Export, J3): ordered accepted items, warnings, download .md/.json, copy, write.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { postJson } from "../lib/api.ts";
import { navigate, type Route } from "../lib/router.ts";
import { TID } from "../lib/testids.ts";
import type { ExportWritten, S } from "../lib/types.ts";

export interface ExportProps {
  route: Extract<Route, { name: "export" }>;
}

export function Export(props: ExportProps) {
  const runRef = props.route.run;
  const search = props.route.search;
  const includeStale = !!search.include_stale;

  const [written, setWritten] = useState<ExportWritten | null>(null);
  const [isWriting, setIsWriting] = useState(false);
  const [error, setError] = useState<S | null>(null);

  const { data: previewText, isLoading } = useQuery({
    queryKey: ["exportPreview", runRef, includeStale],
    queryFn: async () => {
      const qs = `?run=${encodeURIComponent(runRef)}&format=md${includeStale ? "&include_stale=1" : ""}`;
      const res = await fetch(`/api/export${qs}`);
      if (!res.ok) throw new Error(`Failed to load export preview: HTTP ${res.status}`);
      return res.text();
    },
  });

  const handleWrite = async () => {
    setIsWriting(true);
    setError(null);
    try {
      const res = await postJson<ExportWritten>("/api/exports", {
        run: runRef,
        include_stale: includeStale,
      });
      setWritten(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsWriting(false);
    }
  };

  const mdUrl = `/api/export?run=${encodeURIComponent(runRef)}&format=md${includeStale ? "&include_stale=1" : ""}`;
  const jsonUrl = `/api/export?run=${encodeURIComponent(runRef)}&format=json${includeStale ? "&include_stale=1" : ""}`;

  return (
    <section data-view="export" aria-label="Export" className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text">Export Decisions</h1>
          <p className="text-xs text-muted mt-0.5">
            Export accepted decisions for run <span className="font-mono">{runRef}</span> in order: merges, splits, overlaps.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-text-2 cursor-pointer mr-2">
            <input
              type="checkbox"
              checked={includeStale}
              onChange={(e) =>
                navigate({
                  ...props.route,
                  search: { include_stale: e.target.checked ? true : undefined },
                })
              }
              className="rounded border-border text-emerald-600 focus:ring-emerald-500"
            />
            <span>Include stale decisions</span>
          </label>

          <a
            data-testid={TID.downloadMd}
            href={mdUrl}
            download
            className="btn btn-outline text-xs px-3 py-1.5 border rounded hover:bg-surface-2 font-medium"
          >
            Download .md
          </a>

          <a
            data-testid={TID.downloadJson}
            href={jsonUrl}
            download
            className="btn btn-outline text-xs px-3 py-1.5 border rounded hover:bg-surface-2 font-medium"
          >
            Download .json
          </a>

          <button
            type="button"
            data-testid={TID.writeExports}
            onClick={handleWrite}
            disabled={isWriting}
            className="btn btn-primary text-xs px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded font-medium disabled:opacity-50"
          >
            {isWriting ? "Writing…" : "Write to .tesselint/exports/"}
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-950/40 border border-red-300 dark:border-red-800 rounded-lg text-xs text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {written && (
        <div
          data-testid={TID.exportWritten}
          className="p-3 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-300 dark:border-emerald-800 rounded-lg text-xs text-emerald-800 dark:text-emerald-200 font-mono space-y-1"
        >
          <div>Successfully written exports:</div>
          <div>MD: {written.md_path}</div>
          <div>JSON: {written.json_path}</div>
        </div>
      )}

      {/* Preview Card */}
      <div className="card bg-surface border border-border rounded-lg p-4 space-y-2">
        <div className="flex items-center justify-between border-b border-border pb-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">Markdown Preview</h2>
          <span className="text-xs text-muted">Generated from decisions store</span>
        </div>

        {isLoading ? (
          <p className="text-muted text-xs p-4">Loading preview…</p>
        ) : (
          <pre
            data-testid={TID.exportPreview}
            className="p-4 bg-surface-2 rounded border border-border font-mono text-xs text-text overflow-x-auto max-h-[600px] whitespace-pre-wrap leading-relaxed"
          >
            {previewText || "No decisions to export."}
          </pre>
        )}
      </div>
    </section>
  );
}
