// App header (docs/ui/DESIGN.md §2 "Chrome"): run switcher (ref, real/synthetic badge, short hash),
// key status and balance, "source edited: N" chip linking to Re-run, "saved · rev N", theme toggle,
// and the tabs (Review shows its open count). The `/` search box lives here too: Enter opens the
// top story.
import { useId, useMemo, useState, type KeyboardEvent, type RefObject } from "react";
import type { TabTarget } from "../lib/hotkeys.ts";
import { href, navigate, type Route } from "../lib/router.ts";
import { TID } from "../lib/testids.ts";
import type { Theme } from "../lib/theme.ts";
import type { KeyStatus, N, RunBundle, RunSummary, S, StoryRow } from "../lib/types.ts";

export interface HeaderProps {
  route: Route;
  runs: RunSummary[];
  bundle: RunBundle | null;
  keyStatus: KeyStatus | null;
  rev: N | null;
  openCount: N | null;
  theme: Theme;
  onToggleTheme: () => void;
  searchRef?: RefObject<HTMLInputElement | null>;
  onHelp?: () => void;
}

const TABS: readonly [TabTarget, S][] = [
  ["overview", "Overview"],
  ["review", "Review"],
  ["matrix", "Matrix"],
  ["clusters", "Clusters"],
  ["runs", "Runs"],
  ["export", "Export"],
];

const TAB_KEY: Record<TabTarget, S> = {
  overview: "o",
  review: "r",
  matrix: "m",
  clusters: "c",
  runs: "u",
  export: "e",
};

export function tabRoute(tab: TabTarget, run: S | null): Route | null {
  if (tab === "runs") return { name: "runs", search: {} };
  if (run === null) return null;
  switch (tab) {
    case "overview":
      return { name: "overview", run };
    case "review":
      return { name: "review", run, search: {} };
    case "matrix":
      return { name: "matrix", run, search: {} };
    case "clusters":
      return { name: "clusters", run, search: {} };
    case "export":
      return { name: "export", run, search: {} };
  }
}

export function activeTab(route: Route): TabTarget | null {
  switch (route.name) {
    case "overview":
    case "review":
    case "matrix":
    case "clusters":
    case "export":
      return route.name;
    case "runs":
    case "runDiff":
    case "reRun":
    case "job":
      return "runs";
    default:
      return null;
  }
}

function RouteLink({
  to,
  children,
  ...rest
}: { to: Route; children: React.ReactNode } & React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a
      href={href(to)}
      onClick={(e) => {
        if (!e.defaultPrevented && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
          e.preventDefault();
          navigate(to);
        }
      }}
      {...rest}
    >
      {children}
    </a>
  );
}

function switchTo(route: Route, ref: S): Route {
  const tab = activeTab(route);
  return (tab && tab !== "runs" ? tabRoute(tab, ref) : null) ?? { name: "overview", run: ref };
}

export function Header(props: HeaderProps) {
  const { route, runs, bundle, keyStatus, rev, openCount, theme, onToggleTheme, searchRef, onHelp } = props;
  const run = bundle?.run ?? null;
  const current = activeTab(route);
  const status = bundle?.source_status;
  const edits = status ? status.edited.length + status.added.length + status.removed.length : 0;
  const nextTheme = theme === "dark" ? "light" : "dark";

  return (
    <header data-testid={TID.header} className="no-print shrink-0 border-b border-border bg-surface">
      <div className="flex h-12 items-center gap-3 px-4">
        <RouteLink to={{ name: "home" }} className="flex items-center gap-1.5 font-semibold tracking-tight">
          TesseLint
        </RouteLink>
        <RunSwitcher route={route} runs={runs} run={run} />
        <div className="ml-auto flex items-center gap-2">
          {bundle && <StorySearch run={bundle.run.ref} stories={bundle.stories} inputRef={searchRef} />}
          <KeyBadge status={keyStatus} />
          {status && edits > 0 && (
            <RouteLink
              to={{ name: "reRun", search: { source: status.source_id ?? undefined } }}
              data-testid={current === "overview" ? undefined : TID.sourceEditedChip}
              className="chip chip-warn hover:underline text-xs bg-amber-100 dark:bg-amber-950 text-amber-800 dark:text-amber-200 px-2 py-0.5 rounded"
              title={`The source changed since this run: ${status.edited.length} edited, ${status.added.length} added, ${status.removed.length} removed.`}
            >
              source edited: {edits}
            </RouteLink>
          )}
          {rev !== null && (
            <span data-testid={TID.savedRev} className="text-xs text-muted tabular-nums" title="decisions.json revision">
              saved · rev {rev}
            </span>
          )}
          {onHelp && (
            <button
              type="button"
              className="btn btn-ghost btn-icon text-xs px-1.5 py-0.5 border rounded"
              onClick={onHelp}
              aria-label="Keyboard shortcuts"
              aria-keyshortcuts="?"
            >
              ?
            </button>
          )}
          <button
            type="button"
            data-testid={TID.themeToggle}
            className="btn btn-ghost btn-icon text-xs px-2 py-0.5 border rounded"
            onClick={onToggleTheme}
            aria-label={`Switch to the ${nextTheme} theme`}
            aria-keyshortcuts="t"
            title={`${nextTheme === "dark" ? "Dark" : "Light"} theme (t)`}
          >
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>
      </div>
      <nav aria-label="Views" className="px-2 border-t border-border">
        <ul className="flex gap-1">
          {TABS.map(([tab, label]) => {
            const to = tabRoute(tab, run?.ref ?? null);
            const selected = current === tab;
            return (
              <li key={tab}>
                {to ? (
                  <RouteLink
                    to={to}
                    data-testid={TID.tab(tab)}
                    aria-current={selected ? "page" : undefined}
                    className={`tab inline-block px-3 py-1.5 text-xs font-medium border-b-2 ${
                      selected ? "border-emerald-500 font-semibold" : "border-transparent text-text-2 hover:border-border"
                    }`}
                    title={`g ${TAB_KEY[tab]}`}
                  >
                    {label}
                    {tab === "review" && openCount !== null && (
                      <span className="count tabular-nums ml-1 px-1.5 py-0.2 rounded bg-surface-2 text-xs">
                        {openCount}
                        <span className="sr-only"> open</span>
                      </span>
                    )}
                  </RouteLink>
                ) : (
                  <span data-testid={TID.tab(tab)} className="tab tab-disabled inline-block px-3 py-1.5 text-xs text-muted opacity-50">
                    {label}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}

function RunSwitcher({ route, runs, run }: { route: Route; runs: RunSummary[]; run: RunSummary | null }) {
  const id = useId();
  const choices = useMemo(() => {
    const listed = runs.filter((r) => !r.dry_run || r.ref === run?.ref);
    return run && !listed.some((r) => r.ref === run.ref) ? [run, ...listed] : listed;
  }, [runs, run]);
  if (!run) return null;
  return (
    <div data-testid={TID.runSwitcher} className="flex min-w-0 items-center gap-2">
      <label htmlFor={id} className="sr-only">
        Run
      </label>
      <select
        id={id}
        value={run.ref}
        onChange={(event) => navigate(switchTo(route, event.target.value))}
        className="field max-w-[18rem] truncate py-1 px-2 border rounded font-mono text-xs bg-surface"
      >
        {choices.map((r) => (
          <option key={r.ref} value={r.ref}>
            {r.ref}
            {r.status === "complete" ? "" : ` (${r.status})`}
          </option>
        ))}
      </select>
      <span
        data-testid={TID.runKind}
        className={`chip text-xs px-1.5 py-0.5 rounded font-mono ${
          run.kind === "real" ? "bg-emerald-100 text-emerald-800" : "bg-surface-2 text-text-2"
        }`}
        title={run.kind_inferred ? "kind inferred from the run" : undefined}
      >
        {run.kind}
      </span>
      <span className="hidden font-mono text-xs text-muted xl:inline" title={`source snapshot sha256 ${run.snapshot_sha256}`}>
        #{run.snapshot_sha256.slice(0, 7)}
      </span>
    </div>
  );
}

function KeyBadge({ status }: { status: KeyStatus | null }) {
  if (!status) {
    return (
      <span data-testid={TID.keyStatus} className="hidden text-xs text-muted lg:inline">
        key …
      </span>
    );
  }
  const text = !status.present
    ? "no key"
    : status.error
      ? "key present · balance unknown"
      : status.remaining_usd === null
        ? "key present"
        : `key present · US$${status.remaining_usd.toFixed(2)} left`;
  return (
    <span
      data-testid={TID.keyStatus}
      className={`hidden items-center gap-1 text-xs lg:inline-flex ${status.present ? "text-text-2" : "text-amber-600"}`}
      title={status.error ?? "OpenRouter key, checked by the TesseLint CLI"}
    >
      {text}
    </span>
  );
}

export function searchStories(stories: readonly StoryRow[], query: S): StoryRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const rank = (s: StoryRow): N => {
    const id = s.id.toLowerCase();
    if (id === q) return 0;
    if (id.startsWith(q)) return 1;
    if (s.title.toLowerCase().includes(q)) return 2;
    if (s.text.toLowerCase().includes(q)) return 3;
    return 9;
  };
  return stories
    .map((story) => ({ story, score: rank(story) }))
    .filter((hit) => hit.score < 9)
    .sort((x, y) => x.score - y.score || x.story.order - y.story.order)
    .slice(0, 8)
    .map((hit) => hit.story);
}

function StorySearch({
  run,
  stories,
  inputRef,
}: {
  run: S;
  stories: StoryRow[];
  inputRef?: RefObject<HTMLInputElement | null>;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const listId = useId();
  const hits = useMemo(() => searchStories(stories, query), [stories, query]);
  const expanded = open && hits.length > 0;

  const go = (story: StoryRow | undefined) => {
    if (!story) return;
    setQuery("");
    setOpen(false);
    inputRef?.current?.blur();
    navigate({ name: "story", run, id: story.id });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((index) => (hits.length ? (index + step + hits.length) % hits.length : 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(hits[active] ?? hits[0]);
    } else if (event.key === "Escape") {
      setQuery("");
      setOpen(false);
      event.currentTarget.blur();
    }
  };

  return (
    <div className="relative hidden md:block">
      <label className="sr-only" htmlFor={`${listId}-input`}>
        Search stories
      </label>
      <input
        id={`${listId}-input`}
        ref={inputRef}
        data-testid={TID.searchInput}
        type="search"
        role="combobox"
        autoComplete="off"
        spellCheck={false}
        aria-expanded={expanded}
        aria-controls={expanded ? listId : undefined}
        aria-activedescendant={expanded ? `${listId}-${active}` : undefined}
        aria-keyshortcuts="/"
        placeholder="Search stories (/)"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
        onKeyDown={onKeyDown}
        className="field w-48 py-1 px-2 border rounded text-xs bg-surface"
      />
      {expanded && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Stories"
          className="popover absolute right-0 z-40 mt-1 w-96 overflow-hidden rounded-lg border border-border bg-surface py-1 shadow-lg"
        >
          {hits.map((story, index) => (
            <li
              key={story.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              onMouseDown={(event) => {
                event.preventDefault();
                go(story);
              }}
              onMouseEnter={() => setActive(index)}
              className={`flex cursor-pointer items-baseline gap-2 px-3 py-1.5 text-xs hover:bg-surface-2 ${
                index === active ? "bg-surface-2" : ""
              }`}
            >
              <span className="font-mono font-semibold shrink-0">{story.id}</span>
              <span className="min-w-0 truncate">{story.title}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
