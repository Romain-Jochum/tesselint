// Review (docs/ui/DESIGN.md §2 Review, J1 J2): case list, merge/split/overlap panes, decision bar, keys, URL state.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useReducer, useState } from "react";
import { DecisionBar } from "../components/DecisionBar.tsx";
import { MergeOptions, OverlapEvidence, SplitEvidence, type OptionStatus } from "../components/Evidence.tsx";
import { SideBySide, StoryColumn } from "../components/SideBySide.tsx";
import { StoryLink } from "../components/StoryLink.tsx";
import { TrustBanner } from "../components/TrustBanner.tsx";
import { getDecisions, getProposals, getRun, postDecisions, postJson } from "../lib/api.ts";
import {
  caseOptions,
  caseStatus,
  type CaseStatus,
  decisionsReducer,
  effectiveDecisions,
  filterCases,
  INITIAL_DECISIONS,
  orphanedDecisions,
} from "../lib/cases.ts";
import { conflictText, keyLabel, parseKey } from "../lib/labels.ts";
import { href, navigate, type Route, type StatusFilter } from "../lib/router.ts";
import { ATTR, TID } from "../lib/testids.ts";
import type {
  Case,
  DecisionChange,
  DecisionsResponse,
  DecisionView,
  N,
  Proposal,
  S,
  StoryRow,
} from "../lib/types.ts";

export interface ReviewProps {
  route: Extract<Route, { name: "review" }>;
}

export function Review(props: ReviewProps) {
  const queryClient = useQueryClient();
  const runRef = props.route.run;
  const search = props.route.search;
  const statusFilter: StatusFilter = search.status ?? "open";
  const [decisionsState, dispatch] = useReducer(decisionsReducer, INITIAL_DECISIONS);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  const th = search.th;

  const { data: bundle } = useQuery({
    queryKey: ["run", runRef],
    queryFn: () => getRun(runRef),
  });

  const { data: proposalsData } = useQuery({
    queryKey: ["proposals", runRef, th],
    queryFn: () => getProposals(runRef, th),
  });

  const { data: decisionsData } = useQuery({
    queryKey: ["decisions", runRef, th],
    queryFn: () => getDecisions(runRef, th),
  });

  useEffect(() => {
    if (decisionsData) {
      dispatch({ type: "loaded", response: decisionsData });
    }
  }, [decisionsData]);

  const proposalsMap = useMemo(() => {
    const map = new Map<S, Proposal>();
    for (const p of proposalsData?.proposals ?? []) {
      map.set(p.key, p);
    }
    return map;
  }, [proposalsData]);

  const decisionsMap = useMemo(() => {
    const map = new Map<S, DecisionView>();
    for (const d of decisionsData?.decisions ?? []) {
      map.set(d.key, d);
    }
    return map;
  }, [decisionsData]);

  const storiesMap = useMemo(() => {
    const map = new Map<S, StoryRow>();
    for (const s of bundle?.stories ?? []) {
      map.set(s.id, s);
    }
    return map;
  }, [bundle]);

  const allCases = proposalsData?.cases ?? [];
  const filteredCases = useMemo(() => {
    return filterCases(allCases, search, { proposals: proposalsMap, decisions: decisionsMap });
  }, [allCases, search, proposalsMap, decisionsMap]);

  const activeCaseKey = search.case ?? filteredCases[0]?.key ?? "";
  const activeCase = useMemo(() => {
    return allCases.find((c) => c.key === activeCaseKey) ?? null;
  }, [allCases, activeCaseKey]);

  // Options for active case
  const options = useMemo(() => {
    return activeCase ? caseOptions(activeCase, proposalsMap) : [];
  }, [activeCase, proposalsMap]);

  const optIndex = search.opt ? search.opt - 1 : 0;
  const activeOption = options[optIndex] ?? options[0] ?? null;
  const activeOptionKey = activeOption?.key ?? activeCase?.proposals[0] ?? "";

  const currentDecisions = useMemo(() => effectiveDecisions(decisionsState), [decisionsState]);

  const selectCase = (caseKey: S) => {
    navigate({ ...props.route, search: { ...search, case: caseKey, opt: undefined } });
  };

  const selectOption = (optNum: number) => {
    navigate({ ...props.route, search: { ...search, opt: optNum } });
  };

  const advanceCase = () => {
    const idx = filteredCases.findIndex((c) => c.key === activeCaseKey);
    if (idx !== -1 && idx + 1 < filteredCases.length) {
      selectCase(filteredCases[idx + 1]!.key);
    } else {
      const allIdx = allCases.findIndex((c) => c.key === activeCaseKey);
      if (allIdx !== -1 && allIdx + 1 < allCases.length) {
        selectCase(allCases[allIdx + 1]!.key);
      }
    }
  };

  const prevCase = () => {
    const idx = filteredCases.findIndex((c) => c.key === activeCaseKey);
    if (idx > 0) {
      selectCase(filteredCases[idx - 1]!.key);
    } else if (idx === -1) {
      const allIdx = allCases.findIndex((c) => c.key === activeCaseKey);
      if (allIdx > 0) {
        selectCase(allCases[allIdx - 1]!.key);
      }
    }
  };

  const handleSubmit = (changes: DecisionChange[]) => {
    const batch = { id: Date.now(), changes };
    dispatch({ type: "submit", batch });
    setSaveState("saving");

    postDecisions({ run: runRef, thresholds: th, changes })
      .then((res) => {
        dispatch({ type: "saved", id: batch.id, response: res });
        setSaveState("saved");
        setTimeout(() => setSaveState("idle"), 1500);
        void queryClient.invalidateQueries({ queryKey: ["decisions", runRef] });
      })
      .catch(() => {
        dispatch({ type: "failed", id: batch.id });
        setSaveState("failed");
      });

    // Advance to next case after 150ms if action was accept, reject or defer
    if (changes.some((c) => c.action !== undefined)) {
      setTimeout(() => {
        advanceCase();
      }, 150);
    }
  };

  const handleUndo = () => {
    setSaveState("saving");
    postJson<DecisionsResponse>("/api/decisions/undo", { run: runRef, thresholds: th })
      .then((res) => {
        dispatch({ type: "loaded", response: res });
        setSaveState("saved");
        void queryClient.invalidateQueries({ queryKey: ["decisions", runRef] });
      })
      .catch(() => {
        setSaveState("failed");
      });
  };

  const handleRetry = () => {
    if (decisionsState.failed) {
      handleSubmit(decisionsState.failed.changes);
    }
  };

  const orphaned = useMemo(() => {
    return orphanedDecisions(decisionsData?.decisions ?? []);
  }, [decisionsData]);

  // Derive superseded for active case's options
  const hasAcceptedOption = useMemo(() => {
    if (!activeCase || activeCase.kind !== "merge") return false;
    return activeCase.proposals.some((pk) => currentDecisions[pk]?.action === "accept");
  }, [activeCase, currentDecisions]);


  // Titles for labels: live stories, then tombstones, then titles stored with decisions.
  const titleOf = useMemo(() => {
    const titles = new Map<S, S>();
    for (const d of decisionsData?.decisions ?? []) for (const [id, t] of Object.entries(d.titles)) titles.set(id, t);
    for (const t of bundle?.tombstones ?? []) titles.set(t.id, t.title);
    for (const s of bundle?.stories ?? []) titles.set(s.id, s.title);
    return (id: S) => titles.get(id);
  }, [bundle, decisionsData]);

  const orderOf = (id: S) => storiesMap.get(id)?.order ?? Number.MAX_SAFE_INTEGER;

  const optionStatus = (key: S): OptionStatus => {
    const action = currentDecisions[key]?.action;
    if (action) return action;
    return hasAcceptedOption ? "superseded" : "none";
  };

  const conflictStrip = (c: Case) => {
    if (c.conflicts.length === 0) return null;
    const first = allCases.find((x) => x.key === c.conflicts[0]);
    const state = c.conflicts.length === 1 && first ? ` · ${caseStatus(first, decisionsMap)}` : "";
    const text = `${conflictText(c.conflicts, titleOf)}${state}`;
    return (
      <div
        data-testid={TID.conflictStrip}
        className="flex shrink-0 items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
      >
        <span className="line-clamp-2 min-w-0" title={text}>
          {text} · press <kbd className="font-mono font-bold">c</kbd> to jump
        </span>
        <button type="button" onClick={() => selectCase(c.conflicts[0]!)} className="shrink-0 font-medium underline">
          View conflict →
        </button>
      </div>
    );
  };

  const paneHeading = (c: Case) => {
    const ids = parseKey(c.key)?.ids ?? [c.anchor];
    const story = (id: S) => (
      <>
        <StoryLink run={runRef} id={id} title={titleOf(id)} />
        {titleOf(id) && <span className="font-normal text-text-2"> “{titleOf(id)}”</span>}
      </>
    );
    const text =
      c.kind === "merge" ? (
        <>
          Where does {story(c.anchor)} go?{" "}
          <span className="font-normal text-muted">
            {c.proposals.length} option{c.proposals.length === 1 ? "" : "s"}, strongest first
          </span>
        </>
      ) : c.kind === "split" ? (
        <>Should {story(c.anchor)} be split?</>
      ) : (
        <>
          How should{" "}
          {ids.map((id, i) => (
            <span key={id}>
              {i > 0 && (i === ids.length - 1 ? " and " : ", ")}
              <StoryLink run={runRef} id={id} title={titleOf(id)} />
            </span>
          ))}{" "}
          be untangled?
        </>
      );
    return <h2 className="line-clamp-2 shrink-0 text-sm font-semibold [text-wrap:balance]">{text}</h2>;
  };

  const activeProposal = activeCase ? proposalsMap.get(activeCase.proposals[0] ?? "") : undefined;

  return (
    <div data-view="review" aria-label="Review" className="flex flex-col flex-1 min-h-0">
      {bundle && <TrustBanner bundle={bundle} defaultCollapsed />}

      {/* Filter bar */}
      <div className="flex items-center gap-4 px-4 py-2 border-b border-border bg-surface text-xs shrink-0">
        <div className="flex items-center gap-1">
          <span className="text-muted mr-1">Status:</span>
          {(["open", "stale", "decided", "all", "orphaned"] as const).map((st) => (
            <a
              key={st}
              href={href({ ...props.route, search: { ...search, status: st } })}
              data-testid={TID.statusFilter(st)}
              aria-current={statusFilter === st ? "true" : undefined}
              className={`px-2 py-0.5 rounded capitalize ${
                statusFilter === st ? "bg-surface-2 font-semibold text-text" : "text-muted hover:text-text"
              }`}
            >
              {st}
            </a>
          ))}
        </div>
        <div className="flex items-center gap-1 border-l border-border pl-4">
          <span className="text-muted mr-1">Kind:</span>
          {(["merge", "overlap", "split"] as const).map((k) => {
            const count = proposalsData?.proposals.filter((p) => p.kind === k).length ?? 0;
            const isSelected = search.kind === k;
            return (
              <a
                key={k}
                href={href({ ...props.route, search: { ...search, kind: isSelected ? undefined : k } })}
                aria-current={isSelected ? "true" : undefined}
                className={`px-2 py-0.5 rounded capitalize flex items-center gap-1 ${
                  isSelected ? "bg-surface-2 font-semibold text-text" : "text-muted hover:text-text"
                }`}
              >
                <span>{k}</span>
                <span data-testid={TID.kindCount(k)} className="text-muted tabular-nums">
                  {count}
                </span>
              </a>
            );
          })}
        </div>
      </div>

      {/* Main split: 320px case list, case pane */}
      <div className="flex flex-1 min-h-0">
        <aside
          data-testid={TID.caseList}
          aria-label="Cases"
          className="w-80 shrink-0 border-r border-border overflow-y-auto bg-surface"
        >
          {statusFilter === "orphaned" ? (
            <div data-testid={TID.orphanList} className="divide-y divide-border">
              {orphaned.map((d) => {
                const label = keyLabel(d.key, (id) => d.titles[id] ?? titleOf(id));
                return (
                  <div key={d.key} {...{ [ATTR.key]: d.key }} className="p-3 text-xs" title={label.text}>
                    <p className="line-clamp-2">
                      <span className="font-semibold">{label.kind}</span> · <span className="font-mono">{label.ids}</span>
                      {label.title && <span className="text-text-2"> “{label.title}”</span>}
                    </p>
                    <span className="text-muted text-[11px]">
                      {d.action ?? "no action"} · story {d.status} · no longer proposed
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="divide-y divide-border">
              {filteredCases.map((c) => {
                const cStatus = caseStatus(c, decisionsMap);
                const isSelected = c.key === activeCaseKey;
                const label = keyLabel(c.key, titleOf);
                return (
                  <button
                    key={c.key}
                    type="button"
                    data-testid={TID.caseRow}
                    {...{ [ATTR.key]: c.key, [ATTR.status]: cStatus }}
                    aria-current={isSelected ? "true" : undefined}
                    title={label.text}
                    onClick={() => selectCase(c.key)}
                    className={`w-full text-left px-3 py-2 text-xs transition-colors flex items-start gap-2 border-l-2 ${
                      isSelected ? "bg-surface-2 border-l-emerald-600" : "border-l-transparent hover:bg-surface-2/60"
                    }`}
                  >
                    <StatusIcon status={cStatus} />
                    <p className="min-w-0 flex-1 line-clamp-2 leading-snug">
                      <span className="font-semibold">{label.kind}</span> ·{" "}
                      <span className="font-mono font-medium">{label.ids}</span>
                      {label.title && <span className="text-text-2"> “{label.title}”</span>}
                    </p>
                    <span className="shrink-0 text-right text-[11px] leading-snug text-muted tabular-nums">
                      {STRENGTH[c.kind]} {c.strength.toFixed(2)}
                      {c.borderline && <span className="block text-amber-800 dark:text-amber-300">borderline</span>}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </aside>

        {/* Case pane */}
        {activeCase ? (
          <section
            aria-label="Case details"
            data-testid={TID.casePane}
            className="flex-1 min-h-0 min-w-0 flex flex-col gap-3 overflow-y-auto p-4 bg-surface-2/30"
          >
            {paneHeading(activeCase)}
            {conflictStrip(activeCase)}

            {activeCase.kind === "merge" && (
              <>
                <MergeOptions
                  options={options.filter((p): p is Extract<Proposal, { kind: "merge" }> => p.kind === "merge")}
                  activeKey={activeOptionKey}
                  statusOf={optionStatus}
                  onPick={selectOption}
                  titleOf={titleOf}
                  orderOf={orderOf}
                />
                {activeOption?.kind === "merge" &&
                  (() => {
                    const keepStory = storiesMap.get(activeOption.details.keep);
                    const dropStory = storiesMap.get(activeOption.details.drop);
                    if (!keepStory || !dropStory) return <p className="text-xs text-muted">Loading stories…</p>;
                    const oldTexts = decisionsMap.get(activeOption.key)?.old_texts;
                    return (
                      <SideBySide
                        className="flex-1 min-h-40"
                        run={runRef}
                        a={keepStory}
                        b={dropStory}
                        captions={[
                          `keep · bundled ${f2(activeOption.details.atomic_keep)}`,
                          `drop · bundled ${f2(activeOption.details.atomic_drop)}`,
                        ]}
                        oldTexts={oldTexts}
                      />
                    );
                  })()}
              </>
            )}

            {activeCase.kind === "split" && activeProposal?.kind === "split" && (
              <>
                <SplitEvidence run={runRef} d={activeProposal.details} borderline={activeProposal.borderline} titleOf={titleOf} />
                {(() => {
                  const story = storiesMap.get(activeProposal.details.story);
                  if (!story) return null;
                  return (
                    <div className="grid flex-1 min-h-40">
                      <StoryColumn
                        run={runRef}
                        story={story}
                        caption={`bundled ${f2(activeProposal.details.bundled)}`}
                        oldText={decisionsMap.get(activeProposal.key)?.old_texts?.[story.id]}
                      />
                    </div>
                  );
                })()}
                <p className="shrink-0 text-xs text-muted">
                  How to split: press <kbd className="font-mono">n</kbd> and write the proposed parts in a note, one per
                  line; the export turns them into new stories.
                </p>
              </>
            )}

            {activeCase.kind === "overlap" && activeProposal?.kind === "overlap" && (
              <>
                <OverlapEvidence run={runRef} d={activeProposal.details} borderline={activeProposal.borderline} titleOf={titleOf} />
                {(() => {
                  const members = activeProposal.details.members.flatMap((id) => storiesMap.get(id) ?? []);
                  const oldTexts = decisionsMap.get(activeProposal.key)?.old_texts;
                  if (members.length === 2) {
                    return (
                      <SideBySide className="flex-1 min-h-40" run={runRef} a={members[0]!} b={members[1]!} oldTexts={oldTexts} />
                    );
                  }
                  return (
                    <div className="grid flex-1 min-h-40 auto-cols-[minmax(18rem,1fr)] grid-flow-col gap-3 overflow-x-auto">
                      {members.map((s) => (
                        <StoryColumn key={s.id} run={runRef} story={s} oldText={oldTexts?.[s.id]} />
                      ))}
                    </div>
                  );
                })()}
              </>
            )}
          </section>
        ) : (
          <div className="flex-1 min-h-0 flex items-center justify-center p-4 bg-surface-2/30 text-muted text-sm">
            {filteredCases.length === 0 ? "No cases match these filters." : "Select a case from the list"}
          </div>
        )}
      </div>

      {activeCase && (
        <DecisionBar
          caseKey={activeCase.key}
          optionKeys={activeCase.proposals}
          selected={activeOptionKey}
          current={currentDecisions}
          saveState={saveState}
          rev={decisionsState.server?.rev ?? null}
          onSubmit={handleSubmit}
          onUndo={handleUndo}
          onRetry={handleRetry}
          onNext={advanceCase}
          onPrev={prevCase}
          onPick={selectOption}
          onConflict={() => {
            if (activeCase && activeCase.conflicts.length > 0) {
              selectCase(activeCase.conflicts[0]!);
            }
          }}
        />
      )}
    </div>
  );
}

const STRENGTH: Record<Case["kind"], S> = { merge: "m", split: "bundled", overlap: "o" };

const f2 = (n: N | null | undefined) => (n === null || n === undefined ? "–" : n.toFixed(2));

/** Case status as an icon plus a screen-reader word (§5: status colours always carry an icon). */
function StatusIcon({ status }: { status: CaseStatus }) {
  const [icon, cls] =
    status === "decided"
      ? ["✓", "text-emerald-700 dark:text-emerald-400"]
      : status === "stale"
        ? ["⟳", "text-amber-700 dark:text-amber-300"]
        : ["○", "text-muted"];
  return (
    <span className={`w-3 shrink-0 text-center leading-snug ${cls}`} title={status}>
      <span aria-hidden="true">{icon}</span>
      <span className="sr-only">{status}</span>
    </span>
  );
}
