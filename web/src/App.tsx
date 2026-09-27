// App shell (docs/ui/DESIGN.md §5 "Routing and state"): server data in TanStack Query, view state
// in the URL via the in-house router, one view per §2 route.
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Header, tabRoute } from "./components/Header.tsx";
import { JsonModal } from "./components/JsonModal.tsx";
import { getDecisions, getKey, getProposals, getRun, getRuns } from "./lib/api.ts";
import { caseStatus } from "./lib/cases.ts";
import { useHotkeys } from "./lib/hotkeys.ts";
import { navigate, useRoute, type Route } from "./lib/router.ts";
import { applyTheme, prefersDark, readStoredTheme, resolveTheme, storeTheme, toggleTheme, type Theme } from "./lib/theme.ts";
import type { DecisionView, S } from "./lib/types.ts";
import { Clusters } from "./views/Clusters.tsx";
import { Epic } from "./views/Epic.tsx";
import { Export } from "./views/Export.tsx";
import { Job } from "./views/Job.tsx";
import { Matrix } from "./views/Matrix.tsx";
import { Overview } from "./views/Overview.tsx";
import { ReRun } from "./views/ReRun.tsx";
import { Review } from "./views/Review.tsx";
import { RunDiff } from "./views/RunDiff.tsx";
import { Runs } from "./views/Runs.tsx";
import { Story } from "./views/Story.tsx";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Shell />
    </QueryClientProvider>
  );
}

function Shell() {
  const route = useRoute();
  const searchRef = useRef<HTMLInputElement>(null);
  const [jsonModalOpen, setJsonModalOpen] = useState(false);

  // Theme
  const [theme, setTheme] = useState<Theme>(() => resolveTheme(readStoredTheme(), prefersDark()));
  useEffect(() => {
    applyTheme(theme);
    storeTheme(theme);
  }, [theme]);
  const handleToggleTheme = () => setTheme((t) => toggleTheme(t));

  // Runs
  const { data: runsData } = useQuery({
    queryKey: ["runs"],
    queryFn: () => getRuns({ includeDry: true }),
  });

  const runs = runsData?.runs ?? [];
  const latestRef = runsData?.latest ?? runs[0]?.ref ?? null;

  // Current run ref from route or latest
  const runRef = "run" in route && typeof route.run === "string" ? route.run : latestRef;

  // Redirect / to /r/:latest/overview
  useEffect(() => {
    if (route.name === "home" && latestRef) {
      navigate({ name: "overview", run: latestRef }, { replace: true });
    }
  }, [route.name, latestRef]);

  // Key status
  const { data: keyStatus } = useQuery({
    queryKey: ["key"],
    queryFn: () => getKey(),
  });

  // Bundle
  const { data: bundle } = useQuery({
    queryKey: ["run", runRef],
    queryFn: () => getRun(runRef!),
    enabled: !!runRef,
  });

  // Decisions
  const { data: decisionsData } = useQuery({
    queryKey: ["decisions", runRef],
    queryFn: () => getDecisions(runRef!),
    enabled: !!runRef,
  });

  // Proposals
  const { data: proposalsData } = useQuery({
    queryKey: ["proposals", runRef],
    queryFn: () => getProposals(runRef!),
    enabled: !!runRef,
  });

  const decisionsMap = useMemo(() => {
    const map = new Map<S, DecisionView>();
    for (const d of decisionsData?.decisions ?? []) {
      map.set(d.key, d);
    }
    return map;
  }, [decisionsData]);

  const openCount = useMemo(() => {
    if (!proposalsData) return null;
    return proposalsData.cases.filter((c) => caseStatus(c, decisionsMap) !== "decided").length;
  }, [proposalsData, decisionsMap]);

  // Global hotkeys
  useHotkeys("global", (action) => {
    if (action.type === "search") {
      searchRef.current?.focus();
    } else if (action.type === "rawJson") {
      setJsonModalOpen((v) => !v);
    } else if (action.type === "theme") {
      handleToggleTheme();
    } else if (action.type === "tab") {
      const target = tabRoute(action.tab, runRef);
      if (target) navigate(target);
    }
  });

  const isFixedLayout = route.name === "review";

  return (
    <div className={`${isFixedLayout ? "h-screen overflow-hidden" : "min-h-screen"} bg-bg text-text flex flex-col`}>
      <Header
        route={route}
        runs={runs}
        bundle={bundle ?? null}
        keyStatus={keyStatus ?? null}
        rev={decisionsData?.rev ?? null}
        openCount={openCount}
        theme={theme}
        onToggleTheme={handleToggleTheme}
        searchRef={searchRef}
      />
      <main className="flex-1 min-h-0 flex flex-col">
        <View route={route} />
      </main>
      <JsonModal
        open={jsonModalOpen}
        onClose={() => setJsonModalOpen(false)}
        title={`JSON · ${runRef ?? "tesselint"}`}
        value={bundle ?? proposalsData ?? runsData ?? {}}
      />
    </div>
  );
}

function View({ route }: { route: Route }) {
  switch (route.name) {
    case "home":
      return <p className="p-4 text-xs text-muted">Opening the latest run…</p>;
    case "overview":
      return <Overview route={route} />;
    case "review":
      return <Review route={route} />;
    case "matrix":
      return <Matrix route={route} />;
    case "clusters":
      return <Clusters route={route} />;
    case "export":
      return <Export route={route} />;
    case "story":
      return <Story route={route} />;
    case "epic":
      return <Epic route={route} />;
    case "runs":
      return <Runs route={route} />;
    case "runDiff":
      return <RunDiff route={route} />;
    case "reRun":
      return <ReRun route={route} />;
    case "job":
      return <Job route={route} />;
    case "notFound":
      return <p className="p-4 text-xs text-muted">Not found: {route.path}</p>;
    default: {
      const unreachable: never = route;
      return unreachable;
    }
  }
}
