"""Core additions for the web UI: keys, details, derive, run.json fields and the CLI's JSON.

Spec: docs/ui/DESIGN.md §3 (keys), §4.2 (run.json), §4.3 (derive, details), §4.4 (CLI flags),
§7.1, §7.3 (detail shapes) and §7.4 (progress events). Jev is FakeJev at the HTTP boundary; the
`slow` tests load the local model2vec model, as every run does.
"""

import asyncio
import dataclasses
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from tesselint import cli, pipeline
from tesselint.cluster import average_linkage
from tesselint.derive import derive
from tesselint.jev import JevClient, KeyInfo
from tesselint.models import ProposalKind, Story, Thresholds
from tesselint.parse import take_snapshot
from tesselint.proposals import Proposal, proposal_key, propose
from tesselint.protocol import (
    DoneEvent,
    EstimateEvent,
    EstimateLine,
    KeyStatus,
    PhaseEvent,
    RunDirEvent,
)
from tesselint.render import render
from tesselint.server.payloads import MergeDetails, OverlapDetails, SplitDetails
from tesselint.server.runindex import scan_runs
from tesselint.synth import truth_to_dict
from tesselint.verdicts import overlap_matrix, verdicts_from_answers
from tests.fakes import FakeJev
from tests.helpers_pipeline import (
    API_KEY,
    ExpectedJobs,
    SynthWorld,
    build_world,
    expected_jobs,
    force_hf_offline,
    jev_client,
    make_config,
    read_json,
    read_jsonl,
    run_pipeline,
)

K = ProposalKind
CANARY = "sk-or-canary-" + "7" * 24


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` must never read the real .env from these tests."""
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")


def invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the CLI in-process; (exit code, stdout, stderr). A stub's error is re-raised."""
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    out, err = capsys.readouterr()
    if code == 1 and "unexpected NotImplementedError" in err:
        raise NotImplementedError(err.strip())
    return code, out, err


def use_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeJev) -> None:
    """Every JevClient the CLI builds talks to `fake`."""

    def client(api_key: str, **kwargs: Any) -> JevClient:
        return JevClient(api_key, transport=fake.transport, **kwargs)

    monkeypatch.setattr(cli, "JevClient", client)


def without_api_key(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> None:
    """No key in the environment, and no .env within reach."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(workdir)


def tree(root: Path) -> dict[str, bytes]:
    """Every file under `root` with its bytes."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def as_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


# --- planted proposals ----------------------------------------------------------------------------

LONG = "I plan the whole season on one board with sowing dates, rotations and shared tools"
STORIES = (
    Story("US-1.1", "E1", "Plan the season", "MVP", LONG, ("Rotations carry over.",), 1),
    Story("US-1.2", "E1", "Sowing dates", "MVP", "I see the sowing date for each bed.", (), 2),
    Story("US-1.3", "E1", "Water rota", "Lite", "I join a watering rota with my row.", (), 3),
    Story("US-1.4", "E1", "Rota swaps", "Lite", "I swap a watering day with a neighbour.", (), 4),
    Story("US-1.5", "E1", "Rota reminders", "Lite", "I get a reminder before my turn.", (), 5),
    Story(
        "US-2.1",
        "E2",
        "Tool loans and rent reminders",
        "MVP",
        "I borrow a tool from the shed for a slot. I get reminders when my rent is unpaid.",
        ("Slots are two hours.", "Each reminder has a pay-now link.", "Late tools alert."),
        6,
    ),
    Story("US-2.2", "E2", "Tool loans", "MVP", "I borrow a tool from the shed for a slot.", (), 7),
)
ANSWERS = [  # (sent as A, sent as B, order, probabilities as sent)
    ("US-1.1", "US-1.2", "fwd", {"A_contains_B": 0.8, "partial_overlap": 0.15, "unrelated": 0.05}),
    ("US-1.2", "US-1.1", "swap", {"B_contains_A": 0.7, "partial_overlap": 0.2, "unrelated": 0.1}),
    ("US-1.3", "US-1.4", "fwd", {"partial_overlap": 0.8, "same_area_distinct": 0.2}),
    ("US-1.3", "US-1.5", "fwd", {"partial_overlap": 0.75, "same_area_distinct": 0.25}),
    ("US-1.4", "US-1.5", "fwd", {"partial_overlap": 0.7, "same_area_distinct": 0.3}),
    ("US-1.1", "US-1.3", "fwd", {"partial_overlap": 0.45, "unrelated": 0.55}),
    ("US-2.1", "US-2.2", "fwd", {"A_contains_B": 0.7, "partial_overlap": 0.2, "unrelated": 0.1}),
]
ATOMIC: dict[str, float | None] = {s.id: 0.1 for s in STORIES}
ATOMIC |= {"US-1.1": 0.3, "US-2.1": 0.92}
PREFILTER = {("US-1.1", "US-1.2"): (0.81, True)}


def planted() -> list[Proposal]:
    """A containment merge with a swap, a three-story overlap, a bundle kept by a merge."""
    ids = [s.id for s in STORIES]
    verdicts = verdicts_from_answers(ids, ANSWERS)
    clusters = average_linkage(ids, overlap_matrix(ids, verdicts), threshold=Thresholds().cluster)
    return propose(STORIES, verdicts, clusters, ATOMIC, Thresholds(), prefilter=PREFILTER)


def checked(p: Proposal) -> tuple[Proposal, dict[str, Any]]:
    """`p` with its key checked and its details as JSON."""
    assert p.key == proposal_key(p.kind, p.target, p.stories)
    assert p.details is not None
    details: dict[str, Any] = as_json(p.details)
    return p, details


def one(proposals: Sequence[Proposal], kind: ProposalKind) -> tuple[Proposal, dict[str, Any]]:
    """The only proposal of `kind`."""
    [p] = [p for p in proposals if p.kind == kind]
    return checked(p)


def merge_of(proposals: Sequence[Proposal], drop: str) -> tuple[Proposal, dict[str, Any]]:
    """The merge that drops `drop`."""
    [p] = [p for p in proposals if p.kind == K.MERGE and p.stories[1] == drop]
    return checked(p)


def line(p: Proposal, prefix: str) -> str:
    [found] = [e for e in p.evidence if e.startswith(prefix)]
    return found


def edge_stories(edges: Sequence[Mapping[str, Any]]) -> set[str]:
    return {sid for e in edges for sid in (e["a"], e["b"])}


KEY_CASES = [
    pytest.param(K.MERGE, "US-1.1", ("US-1.1", "US-14.5"), "merge:US-1.1>US-14.5", id="merge"),
    pytest.param(K.SPLIT, None, ("US-17.3",), "split:US-17.3", id="split"),
    pytest.param(
        K.OVERLAP,
        "C3",
        ("US-9.2", "US-1.10", "US-1.2"),
        "overlap:US-1.10+US-1.2+US-9.2",
        id="overlap-sorted",
    ),
    pytest.param(
        K.OVERLAP,
        "C7",
        ("US-1.2", "US-9.2", "US-1.10"),
        "overlap:US-1.10+US-1.2+US-9.2",
        id="overlap-no-run-local-id",
    ),
]


@pytest.mark.parametrize(("kind", "target", "stories", "key"), KEY_CASES)
def test_proposal_key(
    kind: ProposalKind, target: str | None, stories: tuple[str, ...], key: str
) -> None:
    """DESIGN §3 Keys: `merge:<keep>><drop>`, `split:<id>`, `overlap:` + sorted IDs by `+`."""
    assert proposal_key(kind, target, stories) == key


def test_merge_details_and_evidence_round_the_cosine() -> None:
    """details.cos matches pairs.jsonl (4 decimals) whichever platform computed the cosine."""
    ids = [s.id for s in STORIES]
    verdicts = verdicts_from_answers(ids, ANSWERS)
    clusters = average_linkage(ids, overlap_matrix(ids, verdicts), threshold=Thresholds().cluster)
    raw = {("US-1.1", "US-1.2"): (0.8149500705, True)}

    p, d = merge_of(
        propose(STORIES, verdicts, clusters, ATOMIC, Thresholds(), prefilter=raw), "US-1.2"
    )

    assert d["cos"] == 0.815
    assert "cos 0.81" in line(p, "fwd ")


def test_propose_fills_a_unique_key_per_proposal() -> None:
    """DESIGN §7.1: propose() fills `Proposal.key`."""
    acted = [p for p in planted() if p.kind != K.LEAVE]

    assert [p.key for p in acted] == [proposal_key(p.kind, p.target, p.stories) for p in acted]
    assert len({p.key for p in acted}) == len(acted) == 4


def test_merge_details_agree_with_the_evidence() -> None:
    """DESIGN §7.3 MergeDetails: the structured twin of the merge's evidence lines."""
    p, d = merge_of(planted(), "US-1.2")
    numbers = line(p, "fwd ")

    assert set(d) == set(MergeDetails.__annotations__)
    assert (d["relation"], d["keep"], d["drop"]) == (p.evidence[0], *p.stories)
    assert d["m"] == p.decisive_value == pytest.approx(0.75)
    assert f"o {d['o']:.2f}" in numbers
    assert (d["fwd"]["A_contains_B"], d["swap"]["A_contains_B"]) == (0.8, 0.7)
    assert "no swap" not in numbers
    assert d["cluster_keep"] == d["cluster_drop"] and d["cluster_keep"] in numbers
    assert d["cos"] == 0.81 and "cos 0.81" in numbers
    assert (d["atomic_keep"], d["atomic_drop"]) == (0.3, 0.1)
    assert f"(bundled {d['atomic_keep']:.2f})" in line(p, "keep ")
    assert edge_stories(d["related"]) == {"US-1.1", "US-1.3"}
    assert "US-1.3" in line(p, "also related: ")
    assert d["alternatives"] == []


def test_overlap_details_agree_with_the_evidence() -> None:
    """DESIGN §7.3 OverlapDetails."""
    p, d = one(planted(), K.OVERLAP)
    edges = line(p, "edges: ")

    assert set(d) == set(OverlapDetails.__annotations__)
    assert (d["cluster"], d["members"]) == (p.target, list(p.stories))
    assert d["members"] == ["US-1.3", "US-1.4", "US-1.5"]
    assert d["mean_o"] == p.decisive_value
    assert len(d["edges"]) == 3
    assert all(f"(o {e['overlap']:.2f})" in edges for e in d["edges"])
    assert "US-1.1" in edge_stories(d["related"])
    assert "US-1.1" in line(p, "also related: ")


def test_split_details_agree_with_the_evidence() -> None:
    """DESIGN §7.3 SplitDetails; `keeps` are the stories it would absorb (§2 Split pane)."""
    p, d = one(planted(), K.SPLIT)
    facts = p.evidence[1]
    story = next(s for s in STORIES if s.id == "US-2.1")

    assert set(d) == set(SplitDetails.__annotations__)
    assert (d["story"], d["bundled"]) == ("US-2.1", p.decisive_value)
    assert d["words"] == len(render(story).split())
    assert f"{d['words']} words" in facts
    assert d["n_subs"] == 3 and "3 sub-bullets" in facts
    assert d["cluster"] is not None and f"in {d['cluster']}" in facts
    assert d["reaches"] == [] and "overlaps" not in facts
    assert d["keeps"] == ["US-2.2"]


# --- derive ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fake_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[SynthWorld, Path]:
    """One complete FakeJev run of a planted synthetic source; tests must not change it."""
    root = tmp_path_factory.mktemp("fake-run")
    with pytest.MonkeyPatch.context() as mp:
        force_hf_offline(mp)
        world = build_world(root)
        run_dir = asyncio.run(run_pipeline(make_config(world.source, root), FakeJev(world.answer)))
    return world, run_dir


ROW_KEYS = ("id", "kind", "target", "stories", "decisive_value", "borderline", "evidence")


def proposal_row(p: Proposal) -> dict[str, Any]:
    """A proposals.json row: the DESIGN §9 fields plus `key` and `details` (UI DESIGN §4.3)."""
    row = {k: getattr(p, k) for k in ROW_KEYS} | {"key": p.key, "details": p.details}
    shaped: dict[str, Any] = as_json(row | {"kind": p.kind.value})
    return shaped


@pytest.mark.slow
@pytest.mark.parametrize(
    "th",
    [Thresholds(), dataclasses.replace(Thresholds(), merge=0.9, cluster=0.7)],
    ids=["run", "merge-0.9"],
)
def test_derive_writes_nothing_and_equals_report(
    fake_run: tuple[SynthWorld, Path], th: Thresholds, tmp_path: Path
) -> None:
    """DESIGN §4.3: derive is report without writes; report writes what derive returns."""
    _, run_dir = fake_run
    before = tree(run_dir)

    derived = derive(run_dir, th)

    assert tree(run_dir) == before
    copied = tmp_path / "copy"
    shutil.copytree(run_dir, copied)
    pipeline.report(copied, th)
    written = read_json(copied / "proposals.json")["proposals"]
    keys = (*ROW_KEYS, "key", "details")
    assert [{k: row[k] for k in keys} for row in written] == [
        proposal_row(p) for p in derived.proposals
    ]
    rows = read_jsonl(run_dir / "stories.jsonl")
    assert [s.id for s in derived.stories] == [r["id"] for r in rows]
    assert dict(derived.atomic) == {r["id"]: r["atomic"] for r in rows}
    assert derived.thresholds == th


# --- run.json -------------------------------------------------------------------------------------


def v1_doc(**top: Any) -> dict[str, Any]:
    """A small valid stories v1 document; `top` overrides top-level fields."""
    story = {"epic": "E1", "tag": "MVP", "details": ["Each slot is two hours."], "status": "live"}
    return {
        "format": "tesselint/stories",
        "version": 1,
        "render": "md-clean/1",
        "source": {"path": "backlog.md", "converter": "tesselint import/0.1.0"},
        "generated_at": "2026-09-26T12:00:00Z",
        "epics": [{"id": "E1", "name": "Tools"}],
        "stories": [
            story | {"id": "US-1.1", "title": "Borrow a tool", "statement": "I book a tool."},
            story | {"id": "US-1.2", "title": "Return a tool", "statement": "I scan it back in."},
            story | {"id": "US-1.3", "title": "Donate a tool", "statement": "I give a tool away."},
        ],
    } | top


KIND_CASES = [
    pytest.param("md", {}, False, "real", id="markdown"),
    pytest.param("md", {}, True, "synthetic", id="markdown-truth-beside"),
    pytest.param("json", {"x-synthetic": True}, False, "synthetic", id="json-x-synthetic"),
    pytest.param(
        "json",
        {"source": {"path": "synth", "converter": "tesselint synth/1"}},
        False,
        "synthetic",
        id="json-synth-converter",
    ),
    pytest.param(
        "json",
        {"source": {"path": "backlog.md", "converter": "llm:some/model"}},
        False,
        "real",
        id="json-llm-converter",
    ),
]


@pytest.mark.slow
@pytest.mark.parametrize(("fmt", "top", "truth_beside", "kind"), KIND_CASES)
def test_run_json_records_source_kind_dry_run_and_input(
    fmt: str,
    top: dict[str, Any],
    truth_beside: bool,
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DESIGN §4.2 new run.json fields, on dry runs."""
    force_hf_offline(monkeypatch)
    expected_input: dict[str, Any] | None = None
    if fmt == "md":
        world = build_world(tmp_path)
        source = world.source
        if truth_beside:
            (tmp_path / "truth.json").write_text(json.dumps(truth_to_dict(world.truth)), "utf-8")
    else:
        doc = v1_doc(**top)
        source = tmp_path / "stories.json"
        source.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        expected_input = {
            "format": "tesselint/stories",
            "version": 1,
            "converter": doc["source"]["converter"],
            "render": "md-clean/1",
        }

    run_dir = asyncio.run(run_pipeline(make_config(source, tmp_path, dry_run=True), FakeJev()))

    [entry] = scan_runs(run_dir.parent)
    assert (entry.kind, entry.kind_inferred, entry.dry_run) == (kind, False, True)
    meta = read_json(run_dir / "run.json")
    assert (meta["kind"], meta["dry_run"]) == (kind, True)
    assert meta["source"]["format"] == fmt
    assert Path(meta["source"]["path"]).resolve() == source.resolve()
    assert meta.get("input") == expected_input


@pytest.mark.slow
def test_a_live_run_is_not_dry(fake_run: tuple[SynthWorld, Path]) -> None:
    world, run_dir = fake_run

    [entry] = scan_runs(run_dir.parent)
    meta = read_json(run_dir / "run.json")

    assert (entry.dry_run, entry.kind, entry.kind_inferred) == (False, "real", False)
    assert (meta["dry_run"], meta["kind"], meta["source"]["format"]) == (False, "real", "md")
    assert Path(meta["source"]["path"]).resolve() == world.source.resolve()


# --- run --estimate-json --------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize(("max_usd", "over_cap"), [("1", False), ("0", True)])
def test_estimate_json_prints_one_line_and_touches_nothing(
    max_usd: str,
    over_cap: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """DESIGN §4.4: one JSON line; no call, no key, no run directory, nothing written."""
    force_hf_offline(monkeypatch)
    world = build_world(tmp_path)
    sha = take_snapshot(world.source, tmp_path / "expected-snapshot").sha256
    fake = FakeJev(world.answer)
    use_fake(monkeypatch, fake)
    without_api_key(monkeypatch, tmp_path)
    before = tree(tmp_path)

    argv = ["run", "--source", str(world.source), "--out", "runs", "--estimate-json"]
    code, out, err = invoke([*argv, "--max-usd", max_usd], capsys)

    [printed] = out.splitlines()
    estimate = json.loads(printed)
    jobs = expected_jobs(world)
    if not over_cap:
        assert code == 0, err
    assert set(estimate) == set(EstimateLine.__annotations__)
    assert (estimate["to_ask"], estimate["cached"]) == (jobs.fwd + jobs.atomic, 0)
    assert estimate["usd"] > 0
    assert (estimate["cap_usd"], estimate["over_cap"]) == (float(max_usd), over_cap)
    assert estimate["snapshot_sha256"] == sha
    assert tree(tmp_path) == before
    assert (fake.requests, fake.key_requests) == (0, 0)


@pytest.mark.slow
def test_estimate_json_works_with_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN §4.4: `--estimate-json --resume RUN` counts the cached answers, writes nothing."""
    force_hf_offline(monkeypatch)
    world = build_world(tmp_path)
    run_dir = asyncio.run(run_pipeline(make_config(world.source, tmp_path), FakeJev(world.answer)))
    before = tree(run_dir)
    without_api_key(monkeypatch, tmp_path)

    code, out, err = invoke(["run", "--resume", str(run_dir), "--estimate-json"], capsys)

    estimate = json.loads(out)
    jobs = expected_jobs(world)
    assert code == 0, err
    assert (estimate["to_ask"], estimate["cached"]) == (0, jobs.fwd + jobs.atomic)
    assert estimate["snapshot_sha256"] == read_json(run_dir / "run.json")["snapshot_sha256"]
    assert tree(run_dir) == before


# --- progress events ------------------------------------------------------------------------------

EVENT_SHAPES: dict[str, type] = {
    "run_dir": RunDirEvent,
    "estimate": EstimateEvent,
    "phase": PhaseEvent,
    "done": DoneEvent,
}


def check_events(
    events: Sequence[Mapping[str, Any]], run_dir: Path, jobs: ExpectedJobs, *, live: bool
) -> None:
    """DESIGN §7.4: run_dir, estimate, phases (live only), then done with run.json's status."""
    assert [set(e) for e in events] == [
        set(EVENT_SHAPES[str(e["event"])].__annotations__) for e in events
    ]
    first, estimate, *phases, done = events
    assert first["event"] == "run_dir"
    assert Path(first["run_dir"]).resolve() == run_dir.resolve()
    assert (estimate["event"], estimate["to_ask"]) == ("estimate", jobs.fwd + jobs.atomic)
    assert dict(done) == {"event": "done", "status": read_json(run_dir / "run.json")["status"]}
    assert all(e["event"] == "phase" for e in phases)
    if not live:
        assert phases == []
        return
    assert {e["phase"] for e in phases} == {"fwd", "atomic", "swap"}
    for name, total in (("fwd", jobs.fwd), ("atomic", jobs.atomic), ("swap", jobs.swap)):
        last = [e for e in phases if e["phase"] == name][-1]
        assert last["done"] == last["total"] == total
    assert all(0 <= e["done"] <= e["total"] for e in phases)
    spent = [e["spent_usd"] for e in phases]
    assert spent == sorted(spent)


@pytest.mark.slow
@pytest.mark.parametrize("live", [True, False], ids=["live", "dry-run"])
def test_progress_jsonl_gets_the_run_events(
    live: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN §4.4: `run --progress-jsonl PATH` appends the §7.4 events; a dry run has no phase."""
    force_hf_offline(monkeypatch)
    world = build_world(tmp_path)
    fake = FakeJev(world.answer)
    use_fake(monkeypatch, fake)
    monkeypatch.chdir(tmp_path)

    argv = ["run", "--source", str(world.source), "--out", "runs", "--rate", "10000"]
    argv += [] if live else ["--dry-run"]
    code, _, err = invoke([*argv, "--progress-jsonl", "progress.jsonl"], capsys)

    assert code == 0, err
    [run_dir] = list((tmp_path / "runs").iterdir())
    check_events(read_jsonl(tmp_path / "progress.jsonl"), run_dir, expected_jobs(world), live=live)
    assert (fake.requests > 0) is live


# --- key ------------------------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("fake_kwargs", "expected"),
    [
        pytest.param({"api_key": API_KEY, "key_usage_start": 0.88}, KeyInfo(0.88, 2.0), id="ok"),
        pytest.param({"api_key": "sk-or-v1-another-key"}, None, id="rejected"),
        pytest.param({"api_key": API_KEY, "key_status": 500}, None, id="server-error"),
    ],
)
async def test_key_info_reads_usage_and_limit(
    fake_kwargs: dict[str, Any], expected: KeyInfo | None
) -> None:
    """DESIGN §4.4, §7.1: `JevClient.key_info()` via the free GET /api/v1/key, None on failure."""
    fake = FakeJev(**fake_kwargs)
    async with jev_client(fake) as client:
        assert await client.key_info() == expected
    assert fake.requests == 0


@pytest.mark.parametrize("case", ["ok", "rejected", "missing"])
def test_key_json_never_prints_the_key(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN §4.4: `key --json` prints a KeyStatus and never the key (AGENTS.md Secrets)."""
    fake = FakeJev(api_key=CANARY if case == "ok" else "sk-or-v1-other", key_usage_start=0.88)
    use_fake(monkeypatch, fake)
    monkeypatch.chdir(tmp_path)
    if case == "missing":
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", CANARY)

    code, out, err = invoke(["key", "--json"], capsys)

    assert "sk-or-" not in out + err
    status = json.loads(out)
    assert set(status) == set(KeyStatus.__annotations__)
    assert fake.requests == 0
    if case == "ok":
        assert code == 0, err
        assert (status["present"], status["limit_usd"], status["error"]) == (True, 2.0, None)
        assert status["usage_usd"] == pytest.approx(0.88)
        assert status["remaining_usd"] == pytest.approx(1.12)
    elif case == "rejected":
        assert (status["present"], status["usage_usd"], status["remaining_usd"]) == (
            True,
            None,
            None,
        )
        assert status["error"]
    else:
        assert (status["present"], status["usage_usd"]) == (False, None)
