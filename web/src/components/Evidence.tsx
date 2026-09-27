// Case evidence (docs/ui/DESIGN.md §2 Review panes, fed by Proposal.details §7.3): the merge options
// as a table (relation in words, m, o, o-band chip, top-2 forward and swap bars, cluster, cosine),
// the split figures and the overlap edge table. Numbers carry the values; bars only echo them.
import type { ReactNode } from "react";
import { mergeRelationWords, relationLabel, topProbs, type TitleOf } from "../lib/labels.ts";
import { ATTR, TID } from "../lib/testids.ts";
import { bandChip } from "../lib/trust.ts";
import type { Edge, MergeDetails, N, OverlapDetails, Probs, Proposal, S, SplitDetails } from "../lib/types.ts";
import { StoryLink } from "./StoryLink.tsx";

type MergeProposal = Extract<Proposal, { kind: "merge" }>;
export type OptionStatus = "accept" | "reject" | "defer" | "superseded" | "none";

const STATUS_TEXT: Record<OptionStatus, S> = {
  accept: "✓ accepted",
  reject: "✗ rejected",
  defer: "⏸ deferred",
  superseded: "superseded",
  none: "",
};
const STATUS_CLASS: Record<OptionStatus, S> = {
  accept: "text-emerald-700 dark:text-emerald-400",
  reject: "text-red-700 dark:text-red-400",
  defer: "text-amber-800 dark:text-amber-300",
  superseded: "text-amber-800 dark:text-amber-300",
  none: "",
};

const f2 = (n: N | null | undefined) => (n === null || n === undefined ? "–" : n.toFixed(2));

/** The o-band chip (§2): how often labelled pairs in this o band were really related. */
export function BandChip({ o }: { o: N }) {
  return (
    <span
      data-testid={TID.bandChip}
      title="Labelled pairs in this o band that were really related (docs/results.md)"
      className="inline-block whitespace-nowrap rounded border border-border bg-surface-2 px-1.5 py-px text-[11px] text-text-2 tabular-nums"
    >
      {bandChip(o)}
    </span>
  );
}

/** Top-2 relation probabilities as small labelled bars; `a`/`b` name A and B (document order). */
function ProbBars({ probs, a, b }: { probs: Probs | null; a: S; b: S }) {
  if (!probs) return <span className="text-muted">not asked</span>;
  return (
    <ul className="space-y-0.5">
      {topProbs(probs).map(([relation, p]) => (
        <li key={relation} className="flex items-center gap-1.5">
          <span className="w-[6.5rem] truncate text-text-2" title={relationLabel(relation, a, b)}>
            {relationLabel(relation, a, b)}
          </span>
          <span aria-hidden="true" className="h-1.5 w-10 shrink-0 overflow-hidden rounded-full bg-surface-2">
            <span className="block h-full rounded-full bg-[#2a78d6]" style={{ width: `${Math.round(p * 100)}%` }} />
          </span>
          <span className="w-7 text-right tabular-nums text-text">{f2(p)}</span>
        </li>
      ))}
    </ul>
  );
}

function clusterText(d: MergeDetails): S {
  if (d.cluster_keep === d.cluster_drop) return d.cluster_keep ?? "none";
  return `${d.cluster_keep ?? "none"} / ${d.cluster_drop ?? "none"}`;
}

export interface MergeOptionsProps {
  options: MergeProposal[];
  activeKey: S;
  statusOf: (key: S) => OptionStatus;
  onPick: (n: N) => void;
  titleOf: TitleOf;
  orderOf: (id: S) => N; // document order, to name A and B in the bars
}

/** The merge group's options, strongest first, one row each; the button in each row picks it (keys 1–9). */
export function MergeOptions(props: MergeOptionsProps) {
  const { options, activeKey, statusOf, onPick, titleOf, orderOf } = props;
  const th = "px-1.5 py-1 font-medium text-muted";
  return (
    <div data-testid={TID.evidence} className="shrink-0 overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Options for this merge group and Jev's evidence for each</caption>
        <thead className="border-b border-border">
          <tr>
            <th scope="col" className={th}>Keep it in</th>
            <th scope="col" className={th}>Relation</th>
            <th scope="col" className={`${th} text-right`} title="Merge probability: duplicate + containment">m</th>
            <th scope="col" className={`${th} text-right`} title="Overlap probability: m + partial overlap">o</th>
            <th scope="col" className={th} title="How often labelled pairs with a similar o were really related">
              Calibration
            </th>
            <th scope="col" className={th}>Forward, top 2</th>
            <th scope="col" className={th}>Swapped, top 2</th>
            <th scope="col" className={th} title="Cluster of the kept story / of the dropped story">Cluster</th>
            <th scope="col" className={`${th} text-right`} title="Pre-filter cosine similarity">cos</th>
          </tr>
        </thead>
        <tbody>
          {options.map((opt, index) => {
            const d = opt.details;
            const status = statusOf(opt.key);
            const active = opt.key === activeKey;
            const keepFirst = orderOf(d.keep) <= orderOf(d.drop);
            const [a, b] = keepFirst ? ["keep", "drop"] : ["drop", "keep"];
            const title = titleOf(d.keep);
            return (
              <tr
                key={opt.key}
                className={`border-b border-border/60 last:border-0 align-top ${active ? "bg-surface-2" : ""}`}
              >
                <td className="px-1.5 py-1.5">
                  <button
                    type="button"
                    data-testid={TID.option(index + 1)}
                    {...{ [ATTR.key]: opt.key, [ATTR.status]: status }}
                    aria-pressed={active}
                    onClick={() => onPick(index + 1)}
                    title={title ? `${d.keep} “${title}”` : d.keep}
                    className={`flex w-44 items-start gap-1.5 rounded-md border px-2 py-1 text-left transition-colors ${
                      active ? "border-emerald-600 bg-surface shadow-sm" : "border-border bg-surface hover:bg-surface-2"
                    }`}
                  >
                    <kbd className="font-mono text-muted">{index + 1}</kbd>
                    <span className="min-w-0 flex-1">
                      <span className="block font-mono font-semibold">{d.keep}</span>
                      {title && <span className="block truncate text-text-2">{title}</span>}
                      {status !== "none" && (
                        <span className={`block font-medium ${STATUS_CLASS[status]}`}>{STATUS_TEXT[status]}</span>
                      )}
                    </span>
                  </button>
                </td>
                <td className="px-1.5 py-1.5 whitespace-nowrap">
                  <span className="block font-medium">{mergeRelationWords(d)}</span>
                  {opt.borderline && <span className="text-amber-800 dark:text-amber-300">borderline</span>}
                </td>
                <td className="px-1.5 py-1.5 text-right font-mono tabular-nums">{f2(d.m)}</td>
                <td className="px-1.5 py-1.5 text-right font-mono tabular-nums">{f2(d.o)}</td>
                <td className="px-1.5 py-1.5">
                  <BandChip o={d.o} />
                </td>
                <td className="px-1.5 py-1.5">
                  <ProbBars probs={d.fwd} a={a} b={b} />
                </td>
                <td className="px-1.5 py-1.5">
                  <ProbBars probs={d.swap} a={a} b={b} />
                </td>
                <td className="px-1.5 py-1.5 whitespace-nowrap font-mono">{clusterText(d)}</td>
                <td className="px-1.5 py-1.5 text-right font-mono tabular-nums">{f2(d.cos)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** A labelled figure in an evidence row. */
function Fact({ label, children, hint }: { label: S; children: ReactNode; hint?: S }) {
  return (
    <span className="text-muted" title={hint}>
      {label} <strong className="font-mono font-medium tabular-nums text-text">{children}</strong>
    </span>
  );
}

const ROW = "flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-surface px-3 py-2 text-xs";

/** Split evidence (§2 Split pane): bundled score, words, sub-bullets, clusters reached, merges it keeps. */
export function SplitEvidence({ run, d, borderline, titleOf }: { run: S; d: SplitDetails; borderline: boolean; titleOf: TitleOf }) {
  return (
    <div data-testid={TID.evidence} className={`${ROW} shrink-0`}>
      <Fact label="bundled" hint="Jev's probability that the story bundles several jobs">{f2(d.bundled)}</Fact>
      <Fact label="words">{d.words}</Fact>
      <Fact label="sub-bullets">{d.n_subs}</Fact>
      <Fact label="cluster">{d.cluster ?? "none"}</Fact>
      <Fact label="reaches" hint="Other clusters its parts overlap">{d.reaches.length ? d.reaches.join(", ") : "no other cluster"}</Fact>
      {d.keeps.length > 0 && (
        <span className="text-muted">
          merges into it:{" "}
          {d.keeps.map((id, i) => (
            <span key={id}>
              {i > 0 && ", "}
              <StoryLink run={run} id={id} title={titleOf(id)} className="font-mono font-medium text-text hover:underline" />
            </span>
          ))}
        </span>
      )}
      {borderline && <span className="font-medium text-amber-800 dark:text-amber-300">borderline</span>}
    </div>
  );
}

/** Overlap evidence (§2 Overlap pane): mean o, band chip, cluster, and the edge table. */
export function OverlapEvidence({ run, d, borderline, titleOf }: { run: S; d: OverlapDetails; borderline: boolean; titleOf: TitleOf }) {
  const td = "px-2 py-1";
  return (
    <div data-testid={TID.evidence} className="shrink-0 rounded-lg border border-border bg-surface text-xs">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-3 py-2">
        <Fact label="mean o">{f2(d.mean_o)}</Fact>
        <BandChip o={d.mean_o} />
        <Fact label="cluster">{d.cluster}</Fact>
        <Fact label="members">{d.members.length}</Fact>
        {borderline && <span className="font-medium text-amber-800 dark:text-amber-300">borderline</span>}
      </div>
      <table className="w-full text-left">
        <caption className="sr-only">Pairs inside this overlap</caption>
        <thead className="text-muted">
          <tr>
            <th scope="col" className={`${td} font-medium`}>Story A</th>
            <th scope="col" className={`${td} font-medium`}>Story B</th>
            <th scope="col" className={`${td} font-medium`}>Relation</th>
            <th scope="col" className={`${td} text-right font-medium`}>o</th>
            <th scope="col" className={`${td} text-right font-medium`}>m</th>
          </tr>
        </thead>
        <tbody>
          {d.edges.map((e: Edge) => (
            <tr key={`${e.a}~${e.b}`} className="border-t border-border/60">
              <td className={td}>
                <StoryLink run={run} id={e.a} title={titleOf(e.a)} />
              </td>
              <td className={td}>
                <StoryLink run={run} id={e.b} title={titleOf(e.b)} />
              </td>
              <td className={td}>{relationLabel(e.relation, e.a, e.b)}</td>
              <td className={`${td} text-right font-mono tabular-nums`}>{f2(e.overlap)}</td>
              <td className={`${td} text-right font-mono tabular-nums`}>{f2(e.merge)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
