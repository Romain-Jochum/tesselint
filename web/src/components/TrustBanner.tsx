// Trust banner (docs/ui/DESIGN.md §2 "Trust banner"): on Overview and Review, collapsible, never
// removed; figures from the run's calibration.json, else the newest same-kind run's (cited), else
// "not calibrated". The o-band sentence uses O_BANDS (docs/results.md figures, lib/trust.ts).
import { useId, useState } from "react";
import { TID } from "../lib/testids.ts";
import { O_BANDS, trustFigures } from "../lib/trust.ts";
import type { B, N, RunBundle, S } from "../lib/types.ts";

export interface TrustBannerProps {
  bundle: Pick<RunBundle, "run" | "calibration" | "calibration_from">;
  defaultCollapsed?: boolean;
}

const num = (values: Record<S, N | B>, name: S): N | null => {
  const value = values[name];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
};
const passed = (values: Record<S, N | B>, name: S): S => (values[name] === true ? "passed" : "failed");

/** "0 of 83 labelled pairs below 0.5 were related; 9 of 10 related pairs had o ≥ 0.7" from O_BANDS. */
function bandSentence(): S {
  const below = O_BANDS.filter((band) => band.hi <= 0.5);
  const related = O_BANDS.reduce((sum, band) => sum + band.related, 0);
  const high = O_BANDS.filter((band) => band.lo >= 0.7).reduce((sum, band) => sum + band.related, 0);
  const belowRelated = below.reduce((sum, band) => sum + band.related, 0);
  const belowPairs = below.reduce((sum, band) => sum + band.pairs, 0);
  return `o ranks well: ${belowRelated} of ${belowPairs} labelled pairs below 0.5 were related; ${high} of ${related} related pairs had o ≥ 0.7.`;
}

export function TrustBanner(props: TrustBannerProps) {
  const [open, setOpen] = useState(!props.defaultCollapsed);
  const detailsId = useId();
  const figures = trustFigures(props.bundle);
  const { values } = figures;
  const kappa = num(values, "rel_kappa");
  const relN = num(values, "rel_n");
  const precision = num(values, "atomic_precision");
  const auc = num(values, "atomic_auc");

  return (
    <aside
      data-testid={TID.trustBanner}
      aria-label="How far to trust the proposals"
      className="trust rounded-lg border px-3 py-2 text-sm bg-surface"
    >
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 text-pretty">
          <strong className="font-semibold">Merges and overlaps are a ranked review queue, not decisions.</strong>{" "}
          {!open && <span className="text-text-2">Each card's o band says how often similar pairs were related.</span>}
        </p>
        <button
          type="button"
          data-testid={TID.trustToggle}
          aria-expanded={open}
          aria-controls={detailsId}
          onClick={() => setOpen((value) => !value)}
          className="btn btn-ghost -my-1 shrink-0 text-xs px-2 py-1 border rounded"
        >
          {open ? "Hide figures" : "Show figures"}
        </button>
      </div>
      {open && (
        <div id={detailsId} className="mt-1 pl-2 text-pretty text-text-2">
          <p>
            {figures.source === "none" ? (
              <>This run is not calibrated: there is no κ for Jev's relation answers, so read every proposal as a lead. </>
            ) : (
              <>
                Jev's relation answers {passed(values, "rel_pass")} calibration (κ {kappa === null ? "–" : kappa.toFixed(2)}
                {relN === null ? "" : `, ${relN} labelled pairs`}).{" "}
              </>
            )}
            {bandSentence()}{" "}
            {figures.source !== "none" && precision !== null && (
              <>
                Splits {passed(values, "atomic_pass")} (precision {precision.toFixed(2)}
                {auc === null ? "" : `, AUC ${auc.toFixed(2)}`}) but are all borderline.
              </>
            )}
          </p>
          <p className="mt-1 text-xs text-muted">Figures measured on one 176-story backlog; see docs/results.md.</p>
          {figures.source === "cited" && (
            <p className="mt-1 text-xs text-muted">
              Figures from run <span className="font-mono">{figures.from}</span>; this run has no calibration of its own.
            </p>
          )}
        </div>
      )}
    </aside>
  );
}
