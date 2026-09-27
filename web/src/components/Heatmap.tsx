// Canvas heatmap (docs/ui/DESIGN.md §5 "Heatmap"; §2 Matrix): one ImageData at 1 px per cell,
// scaled with imageSmoothingEnabled=false, plus an overlay canvas for pointer events; arithmetic hit
// tests (lib/matrix.ts), no DOM per cell. Axis labels, group boundaries (epics or clusters), the
// hovered row and column, and the legend are a handful of absolutely placed elements updated
// through refs, so hover stays within budget. Tooltips use textContent. Values are in [0, 1].
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { hexToRgba, RAMP, rgbaFor, type HeatColors } from "../lib/matrix.ts";
import { ATTR, TID } from "../lib/testids.ts";
import type { N, S } from "../lib/types.ts";

/** A labelled run of display rows or columns (an epic, a cluster). */
export interface HeatGroup {
  start: N;
  count: N;
  label: S | null; // null: unlabelled run (unclustered stories)
  title?: S; // full name on hover
}

export interface HeatLegend {
  caption: S; // "o = probability of overlap"
  ticks: [N, S][]; // positions in [0, 1] and their labels
  threshold?: { at: N; label: S }; // the t marker; cells below it are faded
}

export interface HeatmapProps {
  rows: S[]; // row labels in display order
  cols: S[]; // column labels in display order
  value: (row: N, col: N) => N | null;
  t: N;
  diagonal: boolean; // mask row === col (story level)
  rowGroups?: HeatGroup[];
  colGroups?: HeatGroup[];
  legend?: HeatLegend;
  tooltip: (row: N, col: N) => S;
  label: S; // accessible name
  onHover?: (cell: { row: N; col: N } | null) => void;
  onActivate?: (cell: { row: N; col: N }) => void;
}

const AXIS = 64; // px for the row labels
const MIN_LABEL_PX = 16; // a group narrower than this gets no label

function subscribeTheme(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}
const readDark = () => document.documentElement.getAttribute("data-theme") === "dark";

/** Whether the page is in the dark theme, re-rendering when it switches. */
export function useIsDark(): boolean {
  return useSyncExternalStore(subscribeTheme, readDark, () => false);
}

export function Heatmap(props: HeatmapProps) {
  const { rows, cols, value, t, diagonal, tooltip, label, onHover, onActivate, rowGroups = [], colGroups = [], legend } = props;
  const baseCanvasRef = useRef<HTMLCanvasElement>(null);
  const overlayCanvasRef = useRef<HTMLCanvasElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const rowBandRef = useRef<HTMLDivElement>(null);
  const colBandRef = useRef<HTMLDivElement>(null);
  const cellRef = useRef<HTMLDivElement>(null);
  const rowTagRef = useRef<HTMLSpanElement>(null);
  const colTagRef = useRef<HTMLSpanElement>(null);
  const [plotPx, setPlotPx] = useState(0);
  const isDark = useIsDark();

  const numRows = rows.length;
  const numCols = cols.length;

  useLayoutEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    setPlotPx(plot.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setPlotPx(plot.clientWidth));
    observer.observe(plot);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = baseCanvasRef.current;
    if (!canvas || numRows === 0 || numCols === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const colors: HeatColors = {
      dark: isDark,
      missing: isDark ? hexToRgba("#3a393f") : hexToRgba("#d9d8d3"),
      diagonal: isDark ? hexToRgba("#1f1e22") : hexToRgba("#f4f4f2"),
    };

    const imgData = ctx.createImageData(numCols, numRows);
    const data = imgData.data;
    let offset = 0;
    for (let r = 0; r < numRows; r++) {
      for (let c = 0; c < numCols; c++) {
        const rgba = diagonal && rows[r] === cols[c] ? colors.diagonal : rgbaFor(value(r, c), t, colors);
        data[offset] = rgba[0];
        data[offset + 1] = rgba[1];
        data[offset + 2] = rgba[2];
        data[offset + 3] = rgba[3];
        offset += 4;
      }
    }
    ctx.putImageData(imgData, 0, 0);
  }, [numRows, numCols, rows, cols, value, t, diagonal, isDark]);

  const lastHoveredRef = useRef<{ row: number; col: number } | null>(null);

  useEffect(() => {
    if (lastHoveredRef.current && tooltipRef.current) {
      const { row, col } = lastHoveredRef.current;
      const tipText = tooltip(row, col);
      if (tipText) {
        tooltipRef.current.textContent = tipText;
        tooltipRef.current.style.display = "block";
      }
    }
  }, [tooltip]);

  useEffect(() => {
    const overlay = overlayCanvasRef.current;
    if (!overlay) return;
    const pct = (i: N, n: N) => `${(i / n) * 100}%`;
    const show = (el: HTMLElement | null, on: boolean) => {
      if (el) el.style.display = on ? "block" : "none";
    };

    const onMove = (e: MouseEvent) => {
      if (numRows === 0 || numCols === 0) return;
      const rect = overlay.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const c = Math.min(numCols - 1, Math.max(0, Math.floor((x / rect.width) * numCols)));
      const r = Math.min(numRows - 1, Math.max(0, Math.floor((y / rect.height) * numRows)));
      lastHoveredRef.current = { row: r, col: c };

      const tip = tooltipRef.current;
      if (tip) {
        const tipText = tooltip(r, c);
        tip.textContent = tipText;
        tip.style.display = tipText ? "block" : "none";
        const right = x > rect.width / 2;
        tip.style.left = right ? "" : `${x + 14}px`;
        tip.style.right = right ? `${rect.width - x + 14}px` : "";
        tip.style.top = y > rect.height * 0.7 ? "" : `${y + 14}px`;
        tip.style.bottom = y > rect.height * 0.7 ? `${rect.height - y + 14}px` : "";
      }
      const rowBand = rowBandRef.current;
      const colBand = colBandRef.current;
      const cell = cellRef.current;
      if (rowBand && colBand && cell) {
        rowBand.style.top = cell.style.top = pct(r, numRows);
        rowBand.style.height = cell.style.height = pct(1, numRows);
        colBand.style.left = cell.style.left = pct(c, numCols);
        colBand.style.width = cell.style.width = pct(1, numCols);
        [rowBand, colBand, cell].forEach((el) => show(el, true));
      }
      const rowTag = rowTagRef.current;
      const colTag = colTagRef.current;
      if (rowTag && colTag) {
        rowTag.textContent = rows[r] ?? "";
        rowTag.style.top = pct(r + 0.5, numRows);
        colTag.textContent = cols[c] ?? "";
        colTag.style.left = pct(c + 0.5, numCols);
        show(rowTag, true);
        show(colTag, true);
      }
      onHover?.({ row: r, col: c });
    };

    const onLeave = () => {
      lastHoveredRef.current = null;
      [tooltipRef, rowBandRef, colBandRef, cellRef, rowTagRef, colTagRef].forEach((ref) => show(ref.current, false));
      onHover?.(null);
    };

    overlay.addEventListener("pointermove", onMove);
    overlay.addEventListener("mousemove", onMove);
    overlay.addEventListener("pointerleave", onLeave);
    overlay.addEventListener("mouseleave", onLeave);
    return () => {
      overlay.removeEventListener("pointermove", onMove);
      overlay.removeEventListener("mousemove", onMove);
      overlay.removeEventListener("pointerleave", onLeave);
      overlay.removeEventListener("mouseleave", onLeave);
    };
  }, [numRows, numCols, rows, cols, tooltip, onHover]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const overlay = overlayCanvasRef.current;
    if (!overlay || numRows === 0 || numCols === 0) return;
    const rect = overlay.getBoundingClientRect();
    const c = Math.min(numCols - 1, Math.max(0, Math.floor(((e.clientX - rect.left) / rect.width) * numCols)));
    const r = Math.min(numRows - 1, Math.max(0, Math.floor(((e.clientY - rect.top) / rect.height) * numRows)));
    onActivate?.({ row: r, col: c });
  };

  const colPx = numCols ? plotPx / numCols : 0;
  const rowPx = numRows ? plotPx / numRows : 0;
  const tag = "absolute z-10 hidden whitespace-nowrap rounded border border-text-2 bg-surface px-1 font-mono text-[10px] font-semibold leading-4 text-text shadow-sm";

  return (
    <div
      data-testid={TID.heatmap}
      {...{ [ATTR.rows]: rows.join(","), [ATTR.cols]: cols.join(",") }}
      role="region"
      aria-label={label}
      className="relative flex w-full justify-center p-2"
    >
      <div
        className="grid w-full justify-center gap-x-2"
        style={{
          gridTemplateColumns: legend
            ? `${AXIS}px min(600px, calc(100vh - 270px), calc(100% - ${AXIS + 240}px)) 13rem`
            : `${AXIS}px min(600px, calc(100vh - 270px), calc(100% - ${AXIS + 16}px))`,
          gridTemplateRows: "20px auto",
        }}
      >
        <div />
        {/* Column axis: group labels, then the hovered column's ID */}
        <div className="relative text-[10px] text-text-2" aria-hidden="true">
          {colGroups.map((g) => (
            <span
              key={`c${g.start}`}
              title={g.title}
              className={`absolute bottom-0 top-0 overflow-hidden whitespace-nowrap text-center font-mono leading-5 ${g.start > 0 ? "border-l border-border" : ""}`}
              style={{ left: `${(g.start / numCols) * 100}%`, width: `${(g.count / numCols) * 100}%` }}
            >
              {g.label !== null && g.count * colPx >= MIN_LABEL_PX ? g.label : ""}
            </span>
          ))}
          <span ref={colTagRef} className={`${tag} top-0.5 -translate-x-1/2`} />
        </div>
        {legend && <div />}

        {/* Row axis */}
        <div className="relative text-[10px] text-text-2" aria-hidden="true">
          {rowGroups.map((g) => (
            <span
              key={`r${g.start}`}
              title={g.title}
              className={`absolute left-0 right-0 flex items-center justify-end overflow-hidden whitespace-nowrap pr-1 font-mono ${g.start > 0 ? "border-t border-border" : ""}`}
              style={{ top: `${(g.start / numRows) * 100}%`, height: `${(g.count / numRows) * 100}%` }}
            >
              {g.label !== null && g.count * rowPx >= 12 ? g.label : ""}
            </span>
          ))}
          <span ref={rowTagRef} className={`${tag} right-1 -translate-y-1/2`} />
        </div>

        <div ref={plotRef} className="relative aspect-square w-full border border-border">
          <canvas
            ref={baseCanvasRef}
            data-testid={TID.heatmapBase}
            width={numCols}
            height={numRows}
            className="absolute inset-0 h-full w-full [image-rendering:pixelated]"
          />
          {/* Group boundaries */}
          {colGroups
            .filter((g) => g.start > 0)
            .map((g) => (
              <div
                key={`vc${g.start}`}
                aria-hidden="true"
                className="pointer-events-none absolute bottom-0 top-0 w-px bg-muted/70"
                style={{ left: `${(g.start / numCols) * 100}%` }}
              />
            ))}
          {rowGroups
            .filter((g) => g.start > 0)
            .map((g) => (
              <div
                key={`vr${g.start}`}
                aria-hidden="true"
                className="pointer-events-none absolute left-0 right-0 h-px bg-muted/70"
                style={{ top: `${(g.start / numRows) * 100}%` }}
              />
            ))}
          {/* Hovered row, column and cell */}
          <div ref={rowBandRef} aria-hidden="true" className="pointer-events-none absolute left-0 right-0 hidden bg-text/10 shadow-[inset_0_0_0_1px_var(--text-2)]" />
          <div ref={colBandRef} aria-hidden="true" className="pointer-events-none absolute bottom-0 top-0 hidden bg-text/10 shadow-[inset_0_0_0_1px_var(--text-2)]" />
          <div ref={cellRef} aria-hidden="true" className="pointer-events-none absolute hidden shadow-[0_0_0_1px_var(--surface),0_0_0_3px_var(--text)]" />
          <canvas
            ref={overlayCanvasRef}
            data-testid={TID.heatmapOverlay}
            width={numCols}
            height={numRows}
            onClick={handleClick}
            className="absolute inset-0 h-full w-full cursor-crosshair"
          />
          <div
            ref={tooltipRef}
            data-testid={TID.heatmapTooltip}
            style={{ display: "none" }}
            className="pointer-events-none absolute z-50 w-max max-w-md whitespace-pre-line rounded border border-border bg-surface px-2 py-1 text-xs font-medium leading-5 text-text shadow-md"
          />
        </div>

        {legend && <Legend legend={legend} dark={isDark} />}
      </div>
    </div>
  );
}

/** The colour legend (§5 "The legend marks t"): the 13-step ramp, faded below t, with ticks. */
function Legend({ legend, dark }: { legend: HeatLegend; dark: boolean }) {
  const steps = dark ? [...RAMP].reverse() : RAMP;
  const at = legend.threshold?.at;
  return (
    <figure className="self-start pt-1 text-xs text-text-2">
      <figcaption className="mb-2 font-medium text-text">{legend.caption}</figcaption>
      <div className="relative pt-4">
        {legend.threshold && at !== undefined && (
          <span
            className="absolute top-0 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] font-semibold text-text"
            style={{ left: `${at * 100}%` }}
          >
            {legend.threshold.label}
          </span>
        )}
        <div className="relative flex h-3 overflow-hidden rounded-sm" aria-hidden="true">
          {steps.map((hex) => (
            <span key={hex} className="flex-1" style={{ background: hex }} />
          ))}
          {at !== undefined && at > 0 && (
            <span className="absolute inset-y-0 left-0 bg-surface/75" style={{ width: `${at * 100}%` }} />
          )}
        </div>
        {at !== undefined && (
          <span
            aria-hidden="true"
            className="absolute bottom-[-3px] top-3.5 w-0.5 -translate-x-1/2 bg-text"
            style={{ left: `${at * 100}%` }}
          />
        )}
        <div className="relative mt-1 h-4 font-mono text-[10px] tabular-nums">
          {legend.ticks.map(([pos, text]) => (
            <span
              key={text}
              className={`absolute ${pos <= 0 ? "" : pos >= 1 ? "-translate-x-full" : "-translate-x-1/2"}`}
              style={{ left: `${pos * 100}%` }}
            >
              {text}
            </span>
          ))}
        </div>
      </div>
      {at !== undefined && <p className="mt-1">Cells below t are faded.</p>}
      <p className="mt-1 flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block size-3 rounded-sm bg-cell-missing" /> not answered
      </p>
    </figure>
  );
}
