// Pair drawer (docs/ui/DESIGN.md §2 Matrix: "click or Enter opens the pair drawer (both texts,
// fwd/swap probabilities, decision buttons)"): a focus-trapping dialog.
import { useEffect, useRef } from "react";
import { SideBySide } from "./SideBySide.tsx";
import { TID } from "../lib/testids.ts";
import type { DecisionChange, PairDetail, S, StoryRow } from "../lib/types.ts";

export interface PairDrawerProps {
  run: S;
  pair: PairDetail;
  a: StoryRow;
  b: StoryRow;
  onClose: () => void;
  onDecide?: (changes: DecisionChange[]) => void;
}

export function PairDrawer(props: PairDrawerProps) {
  const { run, pair, a, b, onClose } = props;
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      data-testid={TID.pairDrawer}
      role="dialog"
      aria-modal="true"
      aria-label={`Pair ${pair.a} and ${pair.b}`}
      ref={drawerRef}
      className="fixed inset-y-0 right-0 z-50 w-full max-w-2xl bg-surface border-l border-border shadow-2xl flex flex-col p-4 space-y-4 overflow-y-auto"
    >
      <div className="flex items-center justify-between border-b border-border pb-3">
        <div>
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <span>{pair.a}</span>
            <span className="text-muted">×</span>
            <span>{pair.b}</span>
          </h2>
          <p className="text-xs text-muted tabular-nums mt-0.5">
            overlap {pair.overlap.toFixed(2)} · merge {pair.merge.toFixed(2)} · {pair.relation}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close pair drawer"
          className="btn btn-ghost px-2 py-1 text-sm border rounded"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 min-h-0">
        <SideBySide
          run={run}
          a={a}
          b={b}
          captions={[`o ${pair.overlap.toFixed(2)}`, `m ${pair.merge.toFixed(2)}`]}
        />
      </div>
    </div>
  );
}
