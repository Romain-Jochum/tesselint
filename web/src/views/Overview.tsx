// Overview (docs/ui/DESIGN.md §2 Overview, J8 J6 J5 J10): tiles, what changed, epic health, hubs, print stylesheet.
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { StoryLink } from "../components/StoryLink.tsx";
import { TrustBanner } from "../components/TrustBanner.tsx";
import { getDecisions, getDiff, getMatrix, getProposals, getRun } from "../lib/api.ts";
import { caseStatus } from "../lib/cases.ts";
import { epicHealth, sortHealth, type EpicHealthRow, type HealthKey } from "../lib/health.ts";
import { caseKeyOf, keyLabel, type TitleOf } from "../lib/labels.ts";
import { href, type Route } from "../lib/router.ts";
import { ATTR, TID, type TileName } from "../lib/testids.ts";
import type { DecisionView, N, S } from "../lib/types.ts";

export interface OverviewProps {
  route: Extract<Route, { name: "overview" }>;
}

export function Overview(props: OverviewProps) {
  const runRef = props.route.run;

  const { data: bundle } = useQuery({
    queryKey: ["run", runRef],
    queryFn: () => getRun(runRef),
  });

  const { data: proposalsData } = useQuery({
    queryKey: ["proposals", runRef],
    queryFn: () => getProposals(runRef),
  });

  const { data: decisionsData } = useQuery({
    queryKey: ["decisions", runRef],
    queryFn: () => getDecisions(runRef),
  });

  const { data: matrix } = useQuery({
    queryKey: ["matrix", runRef],
    queryFn: () => getMatrix(runRef),
  });

  const decisionsMap = useMemo(() => {
    const map = new Map<S, DecisionView>();
    for (const d of decisionsData?.decisions ?? []) {
      map.set(d.key, d);
    }
    return map;
  }, [decisionsData]);

  const cases = proposalsData?.cases ?? [];
  const proposals = proposalsData?.proposals ?? [];
  const hubs = proposalsData?.hubs ?? [];

  const mergeCases = useMemo(() => cases.filter((c) => c.kind === "merge"), [cases]);
  const overlapCases = useMemo(() => cases.filter((c) => c.kind === "overlap"), [cases]);
  const splitCases = useMemo(() => cases.filter((c) => c.kind === "split"), [cases]);

  const decidedCount = useMemo(() => {
    return cases.filter((c) => caseStatus(c, decisionsMap) === "decided").length;
  }, [cases, decisionsMap]);

  const staleCount = useMemo(() => {
    return (decisionsData?.decisions ?? []).filter((d) => d.status === "stale").length;
  }, [decisionsData]);

  const mergeProposalsCount = useMemo(() => {
    return proposals.filter((p) => p.kind === "merge").length;
  }, [proposals]);

  const overlapProposalsCount = useMemo(() => {
    return proposals.filter((p) => p.kind === "overlap").length;
  }, [proposals]);

  const splitProposalsCount = useMemo(() => {
    return proposals.filter((p) => p.kind === "split").length;
  }, [proposals]);

  const t = bundle?.thresholds.cluster ?? 0.7;
  const healthRows = useMemo(() => {
    if (!bundle) return [];
    return epicHealth(bundle, proposals, cases, matrix ?? null, t, (c) => caseStatus(c, decisionsMap) === "decided");
  }, [bundle, proposals, cases, matrix, t, decisionsMap]);

  const proposalKeys = useMemo(() => new Set(proposals.map((p) => p.key)), [proposals]);

  const titleOf = useMemo(() => {
    const titles = new Map<S, S>();
    for (const d of decisionsData?.decisions ?? []) for (const [id, title] of Object.entries(d.titles)) titles.set(id, title);
    for (const tomb of bundle?.tombstones ?? []) titles.set(tomb.id, tomb.title);
    for (const s of bundle?.stories ?? []) titles.set(s.id, s.title);
    return (id: S) => titles.get(id);
  }, [bundle, decisionsData]);

  if (!bundle) {
    return (
      <section data-view="overview" aria-label="Overview" className="p-6">
        <p className="text-muted text-sm">Loading run {runRef}…</p>
      </section>
    );
  }

  const { run, source_status: sourceStatus, epics } = bundle;
  const costTotal = (run.cost_live_usd ?? 0) + (run.cost_cached_usd ?? 0);

  return (
    <section data-view="overview" aria-label="Overview" className="p-6 max-w-7xl mx-auto space-y-6">
      <TrustBanner bundle={bundle} />

      {/* Tiles */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Tile
          name="stories"
          title="Stories"
          value={String(run.n_stories)}
          subtitle={`${epics.length} epics`}
        />
        <Tile
          name="merges"
          title="Merges"
          value={`${mergeCases.length} groups · ${mergeProposalsCount} merges`}
          subtitle="by dropped story"
        />
        <Tile
          name="overlaps"
          title="Overlaps"
          value={String(overlapProposalsCount)}
          subtitle={`${overlapCases.length} clusters`}
        />
        <Tile
          name="splits"
          title="Splits"
          value={String(splitProposalsCount)}
          subtitle={`${splitCases.length} stories`}
        />
        <Tile
          name="decided"
          title="Decided"
          value={`${decidedCount}/${cases.length} decided · ${staleCount} stale`}
          subtitle={`${cases.length - decidedCount} open`}
        />
        <Tile
          name="cost"
          title="Cost"
          value={`US$${costTotal.toFixed(2)}`}
          subtitle={`live US$${run.cost_live_usd.toFixed(2)}`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {bundle.previous && (
          <RunChanges run={runRef} previous={bundle.previous} proposalKeys={proposalKeys} titleOf={titleOf} />
        )}

        {/* What changed vs the current source */}
        {sourceStatus && (
          <div
            className={`rounded-lg border border-border bg-surface p-4 space-y-3 ${bundle.previous ? "" : "lg:col-span-3"}`}
          >
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold">Changes in the source since this run</h2>
              <a
                href={href({ name: "reRun", search: { source: sourceStatus.source_id ?? undefined } })}
                className="text-xs text-emerald-700 dark:text-emerald-400 hover:underline font-medium"
              >
                Re-run (estimate first)
              </a>
            </div>
            {sourceStatus.same_snapshot && <p className="text-xs text-muted">The source matches this run's snapshot.</p>}
            {sourceStatus.problems.length > 0 && (
              <p className="text-xs text-amber-800 dark:text-amber-300">
                {sourceStatus.problems.length} unparsed line{sourceStatus.problems.length === 1 ? "" : "s"}: a re-run
                would exit 4.
              </p>
            )}
            <div className="space-y-3 text-xs">
              {(
                [
                  ["edited", "~ Edited", "text-amber-800 dark:text-amber-300"],
                  ["added", "+ Added", "text-emerald-700 dark:text-emerald-400"],
                  ["removed", "− Removed", "text-red-700 dark:text-red-400"],
                ] as const
              ).map(([change, label, cls]) => (
                <div key={change} className="min-w-0">
                  <h3 className={`font-medium ${cls}`}>
                    {label} <span className="tabular-nums">({sourceStatus[change].length})</span>
                  </h3>
                  {change === "edited" ? (
                    // The edited list is itself the way forward: a link to Re-run (estimate first).
                    <a
                      data-testid={TID.sourceChanges(change)}
                      href={href({ name: "reRun", search: { source: sourceStatus.source_id ?? undefined } })}
                      title="Re-run to pick up these edits (estimate first)"
                      className="-mx-1 mt-1 block rounded px-1 py-0.5 hover:bg-surface-2"
                    >
                      <ul className="space-y-1">
                        {sourceStatus.edited.map((sid) => (
                          <li key={sid} {...{ [ATTR.key]: sid }} className="truncate">
                            <span className="font-mono font-semibold">{sid}</span>{" "}
                            <span className="text-text-2">{titleOf(sid)}</span>
                          </li>
                        ))}
                      </ul>
                    </a>
                  ) : (
                    <ul data-testid={TID.sourceChanges(change)} className="mt-1 space-y-1">
                      {sourceStatus[change].map((sid) => (
                        <li key={sid} {...{ [ATTR.key]: sid }} className="truncate" title={titleOf(sid)}>
                          {change === "removed" ? (
                            <span className="font-mono font-semibold">{sid}</span>
                          ) : (
                            <StoryLink run={runRef} id={sid} title={titleOf(sid)} />
                          )}{" "}
                          <span className="text-text-2">{titleOf(sid)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <EpicHealth rows={healthRows} run={runRef} t={t} />

      {/* Hubs */}
      {hubs.length > 0 && (
        <div className="rounded-lg border border-border bg-surface p-4 space-y-2">
          <h2 className="text-sm font-semibold">Hubs: stories kept by 3 or more merges</h2>
          <ul className="flex flex-wrap gap-2 text-xs">
            {hubs.map((hub) => (
              <li
                key={hub.story}
                className="flex max-w-full items-center gap-1.5 rounded border border-border bg-surface-2 px-2.5 py-1"
              >
                <StoryLink run={runRef} id={hub.story} title={titleOf(hub.story)} />
                <span className="max-w-64 truncate text-text-2" title={titleOf(hub.story)}>
                  {titleOf(hub.story)}
                </span>
                <span className="text-muted">· kept by {hub.keeps.length} merges</span>
                {hub.split && (
                  <a
                    href={href({ name: "review", run: runRef, search: { case: hub.split, status: "all" } })}
                    className="font-medium text-amber-800 underline-offset-2 hover:underline dark:text-amber-300"
                  >
                    · also proposed for a split
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

const HEALTH_COLUMNS: { key: HealthKey; label: S; hint: S; numeric: boolean }[] = [
  { key: "epic", label: "Epic", hint: "Document order", numeric: false },
  { key: "live", label: "Stories", hint: "Live stories in this run", numeric: true },
  { key: "inProposals", label: "In proposals", hint: "Stories named by at least one proposal", numeric: true },
  { key: "merges", label: "Merge groups", hint: "Merge groups touching the epic", numeric: true },
  { key: "overlaps", label: "Overlaps", hint: "Overlaps touching the epic", numeric: true },
  { key: "splits", label: "Splits", hint: "Splits touching the epic", numeric: true },
  { key: "within", label: "Pairs o ≥ t", hint: "Pairs inside the epic with o at or above t", numeric: true },
  { key: "partnerPairs", label: "Top partner", hint: "The other epic with the most pairs at or above t", numeric: false },
  { key: "share", label: "Decided", hint: "Share of the epic's cases decided", numeric: true },
];

/** Epic health (§2 Overview): one row per epic, sortable by any column. */
function EpicHealth({ rows, run, t }: { rows: EpicHealthRow[]; run: S; t: N }) {
  const [sort, setSort] = useState<{ key: HealthKey; dir: "asc" | "desc" }>({ key: "epic", dir: "asc" });
  const sorted = useMemo(() => sortHealth(rows, sort.key, sort.dir), [rows, sort]);
  const toggle = (key: HealthKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "epic" ? "asc" : "desc" }));
  const cell = "py-1.5 px-2";
  return (
    <div className="rounded-lg border border-border bg-surface p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold">Epic health</h2>
        <p className="text-xs text-muted">
          t = <span className="font-mono tabular-nums">{t.toFixed(2)}</span> (the run's cluster threshold) · click a
          heading to sort
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs text-left">
          <thead className="border-b border-border text-muted">
            <tr>
              {HEALTH_COLUMNS.map((col) => {
                const active = sort.key === col.key;
                return (
                  <th
                    key={col.key}
                    scope="col"
                    aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                    className={`${cell} font-medium ${col.numeric ? "text-right" : ""}`}
                  >
                    <button
                      type="button"
                      title={col.hint}
                      onClick={() => toggle(col.key)}
                      className={`inline-flex items-center gap-1 rounded hover:text-text ${active ? "text-text" : ""} ${
                        col.numeric ? "flex-row-reverse" : ""
                      }`}
                    >
                      {col.label}
                      <span aria-hidden="true" className={`w-2 ${active ? "" : "opacity-0"}`}>
                        {sort.dir === "asc" ? "↑" : "↓"}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {sorted.map((r) => (
              <tr key={r.epic} className="border-b border-border/50 last:border-0 hover:bg-surface-2/50">
                <td className={`${cell} max-w-72`}>
                  <a href={href({ name: "epic", run, id: r.epic })} className="font-mono font-semibold hover:underline">
                    {r.epic}
                  </a>{" "}
                  <span className="text-text-2" title={r.name}>
                    {r.name}
                  </span>
                </td>
                <td className={`${cell} text-right`}>{r.live}</td>
                <td className={`${cell} text-right`}>{r.inProposals}</td>
                <td className={`${cell} text-right`}>{r.merges}</td>
                <td className={`${cell} text-right`}>{r.overlaps}</td>
                <td className={`${cell} text-right`}>{r.splits}</td>
                <td className={`${cell} text-right`}>{r.within}</td>
                <td className={cell}>
                  {r.partner ? (
                    <a
                      href={href({ name: "matrix", run, search: { level: "stories", rows: r.epic, cols: r.partner } })}
                      className="hover:underline"
                      title={`Open ${r.epic} × ${r.partner} in the matrix`}
                    >
                      <span className="font-mono">{r.partner}</span>{" "}
                      <span className="text-muted">
                        · {r.partnerPairs} pair{r.partnerPairs === 1 ? "" : "s"}
                      </span>
                    </a>
                  ) : (
                    <span className="text-muted">none</span>
                  )}
                </td>
                <td className={`${cell} text-right`}>
                  {r.share === null ? (
                    <span className="text-muted">no cases</span>
                  ) : (
                    <span title={`${r.decided} of ${r.cases} cases`}>
                      {Math.round(r.share * 100)}% <span className="text-muted">of {r.cases}</span>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** "What changed since the previous run" (§2 Overview): the /api/diff of the previous run to this one. */
function RunChanges({ run, previous, proposalKeys, titleOf }: { run: S; previous: S; proposalKeys: ReadonlySet<S>; titleOf: TitleOf }) {
  const { data: diff, error } = useQuery({ queryKey: ["diff", previous, run], queryFn: () => getDiff(previous, run) });
  const caseHref = (key: S, inRun: S) =>
    href({ name: "review", run: inRun, search: { case: caseKeyOf(key), status: "all" } });
  const mark = (m: S) => (
    <span aria-hidden="true" className="inline-block w-3 text-muted">
      {m}
    </span>
  );
  const storyLi = (id: S, inRun: S | null, m: S) => (
    <li key={`${m}${id}`} className="line-clamp-2" title={titleOf(id)}>
      {mark(m)}
      {inRun ? <StoryLink run={inRun} id={id} title={titleOf(id)} /> : <span className="font-mono font-semibold">{id}</span>}{" "}
      <span className="text-text-2">{titleOf(id)}</span>
    </li>
  );
  const proposalLi = (key: S, inRun: S | null, m: S) => {
    const label = keyLabel(key, titleOf);
    const name = (
      <>
        <span className="font-medium">{label.kind}</span> · <Ids text={label.ids} />
      </>
    );
    return (
      <li key={`${m}${key}`} className="line-clamp-2" title={label.text}>
        {mark(m)}
        {inRun ? (
          <a href={caseHref(key, inRun)} className="hover:underline">
            {name}
          </a>
        ) : (
          <span>{name}</span>
        )}
        {label.title && <span className="text-text-2"> “{label.title}”</span>}
      </li>
    );
  };
  return (
    <div className="rounded-lg border border-border bg-surface p-4 space-y-3 lg:col-span-2">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">What changed since the previous run</h2>
        <a
          href={href({ name: "runDiff", search: { a: previous, b: run } })}
          className="text-xs font-medium text-emerald-700 hover:underline dark:text-emerald-400"
          title={`Full diff of ${previous} → ${run}`}
        >
          Full diff →
        </a>
      </div>
      {error ? (
        <p className="text-xs text-red-700 dark:text-red-400">Could not compare with {previous}.</p>
      ) : !diff ? (
        <p className="text-xs text-muted">Comparing with {previous}…</p>
      ) : (
        <div className="grid grid-cols-3 gap-4 text-xs">
          <div className="min-w-0">
            <h3 className="font-medium">
              Stories{" "}
              <span className="font-normal text-muted tabular-nums">
                +{diff.added.length} −{diff.removed.length} ~{diff.edited.length}
                {diff.renamed.length > 0 && ` ↦${diff.renamed.length}`}
              </span>
            </h3>
            <ul className="mt-1 space-y-1">
              {diff.added.map((id) => storyLi(id, run, "+"))}
              {diff.removed.map((id) => storyLi(id, previous, "−"))}
              {diff.edited.map((e) => storyLi(e.id, run, "~"))}
              {diff.renamed.map(([, to]) => storyLi(to, run, "↦"))}
            </ul>
          </div>
          <div className="min-w-0">
            <h3 className="font-medium">
              Proposals{" "}
              <span className="font-normal text-muted tabular-nums">
                {diff.new.length} new · {diff.gone.length} gone · {diff.changed.length} changed
              </span>
            </h3>
            <ul className="mt-1 space-y-1">
              {diff.new.map((key) => proposalLi(key, run, "+"))}
              {diff.gone.map((key) => proposalLi(key, previous, "−"))}
              {diff.changed.map((key) => proposalLi(key, run, "~"))}
            </ul>
          </div>
          <div className="min-w-0">
            <h3 className="font-medium">
              Decisions to re-review{" "}
              <span className="font-normal text-muted tabular-nums">{diff.decisions_to_review.length}</span>
            </h3>
            <ul className="mt-1 space-y-1">
              {diff.decisions_to_review.map((key) => {
                if (proposalKeys.has(key)) return proposalLi(key, run, "⟳");
                const label = keyLabel(key, titleOf);
                return (
                  <li key={key} className="line-clamp-2" title={`${label.text} (no longer proposed)`}>
                    {mark("⟳")}
                    <a href={href({ name: "review", run, search: { status: "orphaned" } })} className="hover:underline">
                      <span className="font-medium">{label.kind}</span> · <Ids text={label.ids} />
                    </a>{" "}
                    <span className="text-muted">no longer proposed</span>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function Tile({
  name,
  title,
  value,
  subtitle,
}: {
  name: TileName;
  title: string;
  value: string;
  subtitle?: string;
}) {
  return (
    <div
      data-testid={TID.tile(name)}
      className="rounded-lg border border-border bg-surface p-3 flex flex-col justify-between"
    >
      <div className="text-xs text-muted font-medium">{title}</div>
      <div className="text-base font-semibold tracking-tight my-1 tabular-nums truncate" title={value}>
        {" "}{value}{" "}
      </div>
      {subtitle && <div className="text-[11px] text-text-2 truncate">{" "}{subtitle}{" "}</div>}
    </div>
  );
}

/** Story IDs in monospace, the joining words ("into", commas) in the body face. */
function Ids({ text }: { text: S }) {
  return (
    <>
      {text.split(/( into |, )/).map((part, i) =>
        i % 2 ? (
          <span key={i}>{part}</span>
        ) : (
          <span key={i} className="font-mono">
            {part}
          </span>
        ),
      )}
    </>
  );
}
