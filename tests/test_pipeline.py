"""`pipeline.run` and `pipeline.report` end to end on a planted synthetic document.

FakeJev stands in for Jev only; its answers come from the Truth (see helpers_pipeline). Every run
computes the local pre-filter (DESIGN §2.2), so these tests load the cached model2vec model with
the Hugging Face hub forced offline.
"""

import csv
import hashlib
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pytest

from tesselint.models import Thresholds
from tesselint.pipeline import report
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.render import text_hash
from tesselint.workers import estimate_tokens
from tests.fakes import RESOLVED_MODEL, USD_PER_TOKEN, FakeJev
from tests.helpers_pipeline import (
    CLUSTER_FIELDS,
    CLUSTERS_FIELDS,
    EDGE_FIELDS,
    PAIR_FIELDS,
    PROPOSAL_FIELDS,
    RUN_FIELDS,
    RUN_FILES,
    RUN_NESTED,
    STORY_FIELDS,
    SynthWorld,
    build_world,
    edit_story,
    expected_jobs,
    force_hf_offline,
    load_world,
    make_config,
    read_json,
    read_jsonl,
    run_pipeline,
    unplanted_story,
    words,
)

pytestmark = [pytest.mark.anyio, pytest.mark.slow]


@pytest.fixture(autouse=True)
def _hf_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    force_hf_offline(monkeypatch)


def missing_files(run_dir: Path) -> list[str]:
    """§9 files absent from a run directory."""
    return [name for name in RUN_FILES if not (run_dir / name).is_file()]


def read_matrix(run_dir: Path) -> list[list[str]]:
    """matrix.csv as rows of cells."""
    with (run_dir / "matrix.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def pair_states(fake: FakeJev) -> list[dict[str, str]]:
    """States of the successful rel.v1 calls FakeJev served."""
    states: list[dict[str, str]] = []
    for call in fake.ok_calls():
        if call.question_id == REL_V1.id:
            assert not isinstance(call.state, str)
            states.append(dict(call.state))
    return states


def story_states(fake: FakeJev) -> list[str]:
    """States of the successful atomic.v2 calls FakeJev served."""
    return [str(c.state) for c in fake.ok_calls() if c.question_id == ATOMIC_V2.id]


def preflight_floor_usd(world: SynthWorld) -> float:
    """Token-model cost of every forward pair and atomic call, before any swap (DESIGN §3)."""
    ids, texts = world.ids, world.texts
    tokens = sum(
        estimate_tokens(words(texts[a]), words(texts[b]))
        for i, a in enumerate(ids)
        for b in ids[i + 1 :]
        if texts[a] != texts[b]
    )
    tokens += sum(estimate_tokens(words(texts[s]), None) for s in ids if words(texts[s]) >= 8)
    return tokens * USD_PER_TOKEN


async def test_run_writes_every_output_file_with_its_fields(tmp_path: Path) -> None:
    """A run writes every §9 file with its fields; snapshot.sha256 matches snapshot.md (§9)."""
    world = build_world(tmp_path)
    cfg = make_config(world.source, tmp_path)

    run_dir = await run_pipeline(cfg, FakeJev(world.answer))

    assert run_dir.parent == cfg.out_root
    assert missing_files(run_dir) == []
    snapshot = (run_dir / "snapshot.md").read_bytes()
    digest = hashlib.sha256(snapshot).hexdigest()
    assert (run_dir / "snapshot.sha256").read_text("utf-8").strip() == f"{digest}  snapshot.md"
    assert snapshot.startswith(b"## 4.") and b"\n## 5." not in snapshot
    stories = read_jsonl(run_dir / "stories.jsonl")
    assert [row["id"] for row in stories] == world.ids
    assert all(row.keys() >= STORY_FIELDS for row in stories)
    assert all(row.keys() >= PAIR_FIELDS for row in read_jsonl(run_dir / "pairs.jsonl"))
    assert read_matrix(run_dir)[0] == ["id", *world.ids]
    clusters = read_json(run_dir / "clusters.json")
    assert clusters.keys() >= CLUSTERS_FIELDS
    assert all(c.keys() >= CLUSTER_FIELDS for c in clusters["clusters"])
    assert all(e.keys() >= EDGE_FIELDS for c in clusters["clusters"] for e in c["edges"])
    proposals = read_json(run_dir / "proposals.json")
    assert set(proposals) == {"thresholds", "proposals"}
    assert all(p.keys() >= PROPOSAL_FIELDS for p in proposals["proposals"])
    assert (run_dir / "proposals.md").read_text("utf-8").startswith("# tesselint proposals")
    run = read_json(run_dir / "run.json")
    assert run.keys() >= RUN_FIELDS
    for key, fields in RUN_NESTED.items():
        assert fields <= run[key].keys(), key
    assert (run["status"], run["snapshot_sha256"]) == ("complete", digest)


async def test_run_issues_exactly_the_designed_jobs(tmp_path: Path) -> None:
    """Forward = n(n-1)/2 minus identical texts, swap only where o >= 0.2, atomic >= 8 words."""
    world = build_world(tmp_path)
    expected = expected_jobs(world)
    fake = FakeJev(world.answer)

    run_dir = await run_pipeline(make_config(world.source, tmp_path), fake)

    assert world.unknown_texts == [], "Jev must receive the rendered full texts"
    run = read_json(run_dir / "run.json")
    assert run["jobs"] == {"fwd": expected.fwd, "swap": expected.swap, "atomic": expected.atomic}
    calls = run["calls"]
    assert (calls["live"], calls["cached"], calls["failed"]) == (expected.total, 0, 0)
    assert (run["n_stories"], run["n_tombstones"]) == (len(world.stories), world.n_tombstones)
    pairs = pair_states(fake)
    assert len(pairs) == expected.fwd + expected.swap
    assert len(story_states(fake)) == expected.atomic
    asked = Counter(frozenset(state.values()) for state in pairs)
    ids, texts = world.ids, world.texts
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if texts[a] != texts[b]:
                swapped = world.overlap(a, b) >= Thresholds().swap_gate
                assert asked[frozenset((texts[a], texts[b]))] == 1 + swapped, (a, b)
    forward_only = [s for s in pairs if asked[frozenset(s.values())] == 1]
    assert all(text_hash(s["A"]) < text_hash(s["B"]) for s in forward_only)
    rel_lines = [r for r in read_jsonl(run_dir / "pairs.jsonl") if r["question"] == REL_V1.id]
    assert len(rel_lines) == expected.fwd + expected.swap
    assert run["questions"] == {REL_V1.id: REL_V1.hash, ATOMIC_V2.id: ATOMIC_V2.hash}
    assert (run["models_resolved"], run["mixed_models"]) == ([RESOLVED_MODEL], False)
    assert run["cost_usd"]["live"] == pytest.approx(fake.total_cost)
    assert run["cost_usd"]["key_usage_delta"] == pytest.approx(fake.total_cost)


async def test_proposals_and_matrix_reflect_the_planted_truth(tmp_path: Path) -> None:
    """Planted duplicates and containments become merges, bundles splits (DESIGN §2.5, §6)."""
    world = build_world(tmp_path)
    truth = world.truth
    assert truth.duplicates and truth.containments and truth.bundled, "n=20 must plant each kind"

    run_dir = await run_pipeline(make_config(world.source, tmp_path), FakeJev(world.answer))

    proposals = read_json(run_dir / "proposals.json")["proposals"]
    merges = [p for p in proposals if p["kind"] == "merge"]
    order = {sid: i for i, sid in enumerate(world.ids)}
    kept_and_gone = [tuple(sorted(pair, key=order.__getitem__)) for pair in truth.duplicates]
    kept_and_gone += list(truth.containments)  # (container, contained): the container is kept
    for kept, gone in kept_and_gone:
        assert any(p["target"] == kept == p["stories"][0] and gone in p["stories"] for p in merges)
    planted = {frozenset(pair) for pair in [*truth.duplicates, *truth.containments]}
    assert all(frozenset((p["target"], s)) in planted for p in merges for s in p["stories"][1:])
    splits = [p for p in proposals if p["kind"] == "split"]
    assert sorted(p["stories"][0] for p in splits) == sorted(truth.bundled)
    assert all(p["target"] is None and len(p["stories"]) == 1 for p in splits)
    run = read_json(run_dir / "run.json")
    assert (run["proposals"]["merge"], run["proposals"]["split"]) == (len(merges), len(splits))
    assert read_json(run_dir / "clusters.json")["clusters"], "planted pairs have o = 1.0"
    rows = read_matrix(run_dir)
    assert [row[0] for row in rows[1:]] == world.ids
    for i, a in enumerate(world.ids):
        for j, b in enumerate(world.ids):
            if i != j:
                assert float(rows[i + 1][j + 1]) == pytest.approx(world.overlap(a, b), abs=0.005)


async def test_identical_second_run_is_served_from_the_cache(tmp_path: Path) -> None:
    """Re-running an unchanged document makes zero calls and yields the same proposals (§2.6)."""
    world = build_world(tmp_path)
    expected = expected_jobs(world)
    first = await run_pipeline(make_config(world.source, tmp_path), FakeJev(world.answer))
    fake = FakeJev(world.answer)

    second = await run_pipeline(make_config(world.source, tmp_path, out="runs2"), fake)

    assert fake.requests == 0
    calls = read_json(second / "run.json")["calls"]
    assert (calls["live"], calls["cached"]) == (0, expected.total)
    assert all(row["cached"] for row in read_jsonl(second / "pairs.jsonl"))
    before = read_json(first / "proposals.json")["proposals"]
    assert read_json(second / "proposals.json")["proposals"] == before


async def test_editing_one_story_reasks_only_that_story(tmp_path: Path) -> None:
    """One edited story costs <= n-1 forward calls and 1 atomic call (DESIGN §2.6)."""
    world = build_world(tmp_path)
    await run_pipeline(make_config(world.source, tmp_path), FakeJev(world.answer))
    story = unplanted_story(world)
    edited_source = tmp_path / "edited.md"
    edited_source.write_text(edit_story(world, story, " It also works offline."), "utf-8")
    edited = load_world(edited_source, world.truth, tmp_path / "oracle-edited")
    new_text = edited.texts[story.id]
    fake = FakeJev(edited.answer)

    await run_pipeline(make_config(edited_source, tmp_path, out="runs2"), fake)

    assert new_text != world.texts[story.id]
    assert edited.unknown_texts == []
    assert story_states(fake) == [new_text]
    pairs = pair_states(fake)
    assert 0 < len(pairs) <= len(world.stories) - 1
    assert all(new_text in state.values() for state in pairs)


async def test_dry_run_writes_an_estimate_without_any_call(tmp_path: Path) -> None:
    """--dry-run makes no HTTP call and writes the preflight estimate (DESIGN §2.7, §7)."""
    world = build_world(tmp_path)
    fake = FakeJev(world.answer)

    run_dir = await run_pipeline(make_config(world.source, tmp_path, dry_run=True), fake)

    assert (fake.requests, fake.key_requests) == (0, 0)
    run = read_json(run_dir / "run.json")
    assert run["calls"]["live"] == 0
    assert run["cost_usd"]["preflight_estimate"] >= 0.99 * preflight_floor_usd(world)


async def test_estimate_above_the_spend_cap_stops_before_any_call(tmp_path: Path) -> None:
    """An estimate above --max-usd stops the run before any Jev call (status partial_cap, §2.7)."""
    world = build_world(tmp_path)
    fake = FakeJev(world.answer)

    run_dir = await run_pipeline(make_config(world.source, tmp_path, max_usd=1e-6), fake)

    assert fake.requests == 0
    run = read_json(run_dir / "run.json")
    assert (run["status"], run["calls"]["live"]) == ("partial_cap", 0)
    assert run["cost_usd"]["preflight_estimate"] > 1e-6


async def test_report_rewrites_proposals_with_new_thresholds_offline(tmp_path: Path) -> None:
    """report() re-derives proposals from the run directory with no HTTP call (DESIGN §7)."""
    world = build_world(tmp_path)
    fake = FakeJev(world.answer)
    run_dir = await run_pipeline(make_config(world.source, tmp_path), fake)
    original = read_json(run_dir / "proposals.json")
    original_md = (run_dir / "proposals.md").read_text("utf-8")
    requests = (fake.requests, fake.key_requests)
    strict = Thresholds(merge=0.95, split=0.95)  # the oracle's m and atomic are 0.9

    report(run_dir, strict)
    rewritten = read_json(run_dir / "proposals.json")
    rewritten_md = (run_dir / "proposals.md").read_text("utf-8")
    report(run_dir, Thresholds())
    restored = read_json(run_dir / "proposals.json")

    assert (fake.requests, fake.key_requests) == requests
    assert rewritten["thresholds"] == asdict(strict)
    assert {p["kind"] for p in rewritten["proposals"]}.isdisjoint({"merge", "split"})
    assert rewritten_md != original_md
    assert restored == original
