"""Builders shared by the workers, pipeline, CLI and outputs tests (DESIGN §6, §8, §9).

`SynthWorld` is a planted synthetic §4 on disk plus an oracle for FakeJev derived from its Truth:
a planted duplicate answers `duplicate`, a containment the right direction, a bundled story
atomic yes 0.9, anything else `unrelated` / no 0.1. Texts map back to IDs by rendering the parsed
stories, so the oracle also checks that the pipeline sends exactly the rendered full texts.

Everything here that touches `tesselint` is a plain function called from inside a test, never a
fixture, so a stub raising NotImplementedError shows up as a test failure, not a setup error.
"""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import huggingface_hub.constants
import pytest

from tesselint import pipeline
from tesselint.config import DEFAULT_MODEL, RunConfig
from tesselint.jev import JevClient
from tesselint.models import Relation, Story, Thresholds
from tesselint.parse import parse_section, take_snapshot
from tesselint.questions import ATOMIC_V2
from tesselint.render import render
from tesselint.synth import Truth, generate
from tests.fakes import FakeJev, State

API_KEY = "sk-or-v1-test-not-a-real-key"
BASE_URL = "https://jev.test/api"
FAST_RATE = 10_000.0  # requests/s: high enough that the token bucket never slows a test

RUN_FILES = (
    "snapshot.md",
    "snapshot.sha256",
    "stories.jsonl",
    "pairs.jsonl",
    "matrix.csv",
    "clusters.json",
    "proposals.md",
    "proposals.json",
    "run.json",
)
STORY_FIELDS = frozenset(
    {"id", "epic", "title", "tag", "line", "words", "text_hash", "text", "atomic", "atomic_skipped"}
)
PAIR_FIELDS = frozenset(
    {
        "a",
        "b",
        "order",
        "question",
        "question_hash",
        "text_hash_a",
        "text_hash_b",
        "choice",
        "confidence",
        "probabilities",
        "model",
        "input_tokens",
        "cost_usd",
        "latency_ms",
        "cached",
        "gen_id",
        "prefilter_cos",
        "prefilter_kept",
    }
)
RUN_FIELDS = frozenset(
    {
        "run_id",
        "started_at",
        "finished_at",
        "wall_s",
        "status",
        "snapshot_sha256",
        "tier",
        "epics",
        "n_stories",
        "n_tombstones",
        "n_pairs",
        "render",
        "jobs",
        "calls",
        "tokens",
        "cost_usd",
        "latency_ms",
        "calls_per_s",
        "concurrency",
        "rate_per_s",
        "max_usd",
        "model_requested",
        "models_resolved",
        "mixed_models",
        "questions",
        "prefilter",
        "thresholds",
        "proposals",
        "versions",
    }
)
RUN_NESTED: Mapping[str, frozenset[str]] = {
    "jobs": frozenset({"fwd", "swap", "atomic"}),
    "calls": frozenset({"live", "cached", "failed", "retries", "by_status"}),
    "tokens": frozenset({"total", "mean", "p95"}),
    "cost_usd": frozenset({"live", "cached_equivalent", "preflight_estimate", "key_usage_delta"}),
    "latency_ms": frozenset({"p50", "p95"}),
    "prefilter": frozenset(
        {"enabled", "k", "top_pct", "kept", "recall_vs_jev_overlap", "recall_vs_jev_merge"}
    ),
    "proposals": frozenset({"merge", "overlap", "split", "leave"}),
}
CLUSTERS_FIELDS = frozenset({"method", "threshold", "clusters", "singletons"})
CLUSTER_FIELDS = frozenset({"id", "members", "mean_overlap", "edges"})
EDGE_FIELDS = frozenset({"a", "b", "overlap", "merge", "relation"})
PROPOSAL_FIELDS = frozenset(
    {"id", "kind", "target", "stories", "decisive_value", "borderline", "evidence"}
)


def force_hf_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make model2vec load `potion-retrieval-32M` from the local HF cache only (no network)."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_OFFLINE", True)


def read_json(path: Path) -> Any:
    """Parse one JSON file."""
    return json.loads(path.read_text("utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSON-lines file, skipping blank lines."""
    lines = path.read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@asynccontextmanager
async def jev_client(fake: FakeJev, *, model: str = DEFAULT_MODEL) -> AsyncIterator[JevClient]:
    """A real JevClient whose HTTP goes to `fake`; closed on exit."""
    client = JevClient(API_KEY, base_url=BASE_URL, model=model, transport=fake.transport)
    try:
        yield client
    finally:
        await client.aclose()


@dataclass
class SynthWorld:
    """A synthetic source on disk, its Truth, its parsed stories and a FakeJev oracle."""

    source: Path
    truth: Truth
    stories: tuple[Story, ...]
    n_tombstones: int
    texts: dict[str, str]  # story id -> rendered full text
    by_text: dict[str, str]  # rendered full text -> story id
    unknown_texts: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        """Story IDs in document order."""
        return [story.id for story in self.stories]

    def relation(self, a: str, b: str) -> Relation:
        """The planted relation with `a` sent as A and `b` as B."""
        if any({a, b} == {x, y} for x, y in self.truth.duplicates):
            return Relation.DUPLICATE
        if (a, b) in self.truth.containments:
            return Relation.A_CONTAINS_B
        if (b, a) in self.truth.containments:
            return Relation.B_CONTAINS_A
        return Relation.UNRELATED

    def overlap(self, a: str, b: str) -> float:
        """o the oracle implies: 1.0 for planted pairs and identical texts, else 0.0 (§2.1)."""
        if self.texts[a] == self.texts[b]:
            return 1.0
        return 0.0 if self.relation(a, b) is Relation.UNRELATED else 1.0

    def story_id(self, text: str) -> str:
        """Map a text Jev received back to its story; record texts that match no story."""
        if text not in self.by_text:
            self.unknown_texts.append(text)
            return "?"
        return self.by_text[text]

    def answer(self, state: State, question_id: str) -> Mapping[str, float]:
        """FakeJev answer function derived from the Truth."""
        if question_id == ATOMIC_V2.id:
            assert isinstance(state, str)
            return {"yes": 0.9 if self.story_id(state) in self.truth.bundled else 0.1}
        assert not isinstance(state, str)
        relation = self.relation(self.story_id(state["A"]), self.story_id(state["B"]))
        if relation is Relation.UNRELATED:
            return {"unrelated": 0.9, "same_area_distinct": 0.1}
        return {relation.value: 0.9, "partial_overlap": 0.1}


def load_world(source: Path, truth: Truth, scratch: Path) -> SynthWorld:
    """Parse and render `source` the way a run would, to build the oracle's text index."""
    scratch.mkdir(parents=True, exist_ok=True)
    parsed = parse_section(take_snapshot(source, scratch).text)
    texts = {story.id: render(story) for story in parsed.stories}
    by_text = {text: sid for sid, text in texts.items()}
    return SynthWorld(source, truth, parsed.stories, len(parsed.tombstones), texts, by_text)


def build_world(root: Path, *, seed: int = 42, n: int = 20) -> SynthWorld:
    """Generate a synthetic document, write it as `root/stories.md` and index it (DESIGN §6)."""
    document, truth = generate(seed=seed, n=n)
    source = root / "stories.md"
    source.write_text(document, encoding="utf-8")
    return load_world(source, truth, root / "oracle")


def make_config(
    source: Path, root: Path, *, out: str = "runs", dry_run: bool = False, max_usd: float = 1.0
) -> RunConfig:
    """A full-matrix, swap-on, default-threshold RunConfig writing under `root` (DESIGN §7)."""
    return RunConfig(
        source=source,
        out_root=root / out,
        epics=None,
        render="full",
        prefilter=False,
        prefilter_k=10,
        swap=True,
        concurrency=8,
        rate_per_s=FAST_RATE,
        max_usd=max_usd,
        cache_dir=root / "cache",
        model=DEFAULT_MODEL,
        base_url=BASE_URL,
        resume=None,
        refresh=False,
        dry_run=dry_run,
        thresholds=Thresholds(),
    )


async def run_pipeline(cfg: RunConfig, fake: FakeJev) -> Path:
    """Run `pipeline.run` with a JevClient on `fake`; return the run directory."""
    async with jev_client(fake, model=cfg.model) as client:
        return await pipeline.run(cfg, client)


@dataclass(frozen=True, slots=True)
class ExpectedJobs:
    """Jobs a cold run of a world must issue."""

    fwd: int
    swap: int
    atomic: int

    @property
    def total(self) -> int:
        """All jobs."""
        return self.fwd + self.swap + self.atomic


def words(text: str) -> int:
    """Whitespace word count of a rendered text."""
    return len(text.split())


def expected_jobs(world: SynthWorld, th: Thresholds | None = None) -> ExpectedJobs:
    """n(n-1)/2 forward pairs minus identical texts, swap where o >= gate, atomic >= 8 words."""
    th = th or Thresholds()
    ids = world.ids
    fwd = swap = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if world.texts[a] == world.texts[b]:
                continue
            fwd += 1
            swap += world.overlap(a, b) >= th.swap_gate
    atomic = sum(words(world.texts[sid]) >= th.min_words_atomic for sid in ids)
    return ExpectedJobs(fwd=fwd, swap=swap, atomic=atomic)


def unplanted_story(world: SynthWorld) -> Story:
    """The first story with a core that is in no planted duplicate, containment or bundle."""
    planted = {sid for pair in world.truth.duplicates for sid in pair}
    planted |= {sid for pair in world.truth.containments for sid in pair}
    planted |= set(world.truth.bundled)
    return next(s for s in world.stories if s.id not in planted and s.core.strip())


def edit_story(world: SynthWorld, story: Story, suffix: str) -> str:
    """Return the source with `suffix` appended to the story's line (Story.line is in §4)."""
    lines = world.source.read_text("utf-8").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("## 4."))
    at = start + story.line - 1
    assert story.id in lines[at], "Story.line must point at the story's own line in snapshot.md"
    body = lines[at].rstrip("\n")
    lines[at] = body + suffix + lines[at][len(body) :]
    return "".join(lines)
