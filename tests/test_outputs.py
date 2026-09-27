"""`write_outputs` on small planted inputs: four stories, one merge, one split (DESIGN §9).

Planted layout (document order US-1.1, US-1.2, US-2.1, US-2.2):
- US-1.1 x US-1.2: fwd sent as (US-1.2, US-1.1), swap cached; o 0.9876, duplicate; cluster C1.
- US-1.1 x US-2.1: o 0.1234.  US-1.2 x US-2.1: o 0.0.  US-1.2 x US-2.2: failed after 6 attempts.
- Pairs with US-2.2 were never answered, so their matrix cells are empty.
- atomic: US-1.1 0.10, US-1.2 0.12, US-2.1 0.85 (split); US-2.2 has 5 words, so it is skipped.
- run_meta carries the run.json fields the pipeline knows, with a non-default cluster threshold.
"""

import csv
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pytest

from tesselint.cluster import Cluster
from tesselint.config import DEFAULT_MODEL
from tesselint.jev import Answer
from tesselint.models import Order, ProposalKind, Relation, Story, Thresholds
from tesselint.outputs import write_outputs
from tesselint.proposals import Proposal
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.render import render, text_hash
from tesselint.verdicts import PairVerdict
from tesselint.workers import Job, JobResult
from tests.fakes import RESOLVED_MODEL
from tests.helpers_pipeline import PAIR_FIELDS, RUN_FIELDS, STORY_FIELDS, read_json, read_jsonl

TH = Thresholds(cluster=0.6)
STORIES = (
    Story(
        "US-1.1",
        "E1",
        "Sign up with email",
        "MVP",
        "I create an account with my email address, so that my loans are kept between visits.",
        ("A confirmation email is sent within a minute.",),
        5,
    ),
    Story(
        "US-1.2",
        "E1",
        "Register by email",
        "MVP",
        "I register with my email address, so that I can come back to my saved profile later.",
        (),
        7,
    ),
    Story(
        "US-2.1",
        "E2",
        "Import and export my loans",
        "Later",
        "I import my loans from a CSV file and export my history as a CSV file, so I keep a copy.",
        (),
        12,
    ),
    Story("US-2.2", "E2", "Delete account", "MVP", "I delete everything.", (), 14),
)
IDS = [story.id for story in STORIES]
MATRIX = {
    ("US-1.1", "US-1.2"): "0.988",
    ("US-1.1", "US-2.1"): "0.123",
    ("US-1.2", "US-2.1"): "0.000",
}
M1 = Proposal(
    "M1",
    ProposalKind.MERGE,
    "US-1.1",
    ("US-1.1", "US-1.2"),
    0.89,
    False,
    ("fwd duplicate 0.90 / partial 0.08", "swap duplicate 0.88 / partial 0.10", "C1"),
)
S1 = Proposal("S1", ProposalKind.SPLIT, None, ("US-2.1",), 0.85, False, ("atomic 0.85",))


@dataclass(frozen=True, slots=True)
class Planted:
    """Inputs handed to write_outputs."""

    results: tuple[JobResult, ...]
    verdicts: dict[tuple[str, str], PairVerdict]
    run_meta: dict[str, Any]


def _story(sid: str) -> Story:
    return next(story for story in STORIES if story.id == sid)


def _answer(question_id: str, probs: dict[str, float], gen_id: str) -> Answer:
    choice = max(probs, key=probs.__getitem__) if question_id == REL_V1.id else None
    return Answer(
        question_id=question_id,
        choice=choice,
        confidence=None if choice is None else probs[choice] - 0.02,
        probabilities=probs,
        model=RESOLVED_MODEL,
        input_tokens=412,
        cost_usd=412 * 4.2e-8,
        gen_id=gen_id,
        latency_ms=301.0,
    )


def _pair(
    a: str, b: str, order: Order, probs: dict[str, float] | None, gen_id: str, *, cached: bool
) -> JobResult:
    text_a, text_b = render(_story(a)), render(_story(b))
    job = Job(
        key=f"key-{order}-{a}-{b}",
        a=a,
        b=b,
        order=order,
        state={"A": text_a, "B": text_b},
        question=REL_V1,
        est_tokens=700,
        text_hash_a=text_hash(text_a),
        text_hash_b=text_hash(text_b),
    )
    if probs is None:
        return JobResult(job, None, cached=False, attempts=6, error="HTTP 503 after 6 attempts")
    return JobResult(job, _answer(REL_V1.id, probs, gen_id), cached=cached, attempts=1, error=None)


def _atomic(sid: str, yes: float) -> JobResult:
    text = render(_story(sid))
    job = Job(
        key=f"key-atomic-{sid}",
        a=sid,
        b=None,
        order="fwd",
        state=text,
        question=ATOMIC_V2,
        est_tokens=400,
        text_hash_a=text_hash(text),
        text_hash_b=None,
    )
    answer = _answer(ATOMIC_V2.id, {"yes": yes}, f"gen-atomic-{sid}")
    return JobResult(job, answer, cached=False, attempts=1, error=None)


def _verdict(a: str, b: str, relation: Relation, overlap: float, merge: float) -> PairVerdict:
    probs = {relation: max(overlap, 1.0 - overlap)}
    return PairVerdict(a, b, probs, probs, None, overlap, merge, relation)


def planted() -> Planted:
    """Build the inputs of the module docstring (render and text_hash are real)."""
    results = (
        _pair(
            "US-1.2",
            "US-1.1",
            "fwd",
            {"duplicate": 0.9, "partial_overlap": 0.08},
            "g1",
            cached=False,
        ),
        _pair(
            "US-1.1",
            "US-1.2",
            "swap",
            {"duplicate": 0.88, "partial_overlap": 0.1},
            "g2",
            cached=True,
        ),
        _pair(
            "US-1.1",
            "US-2.1",
            "fwd",
            {"same_area_distinct": 0.8, "partial_overlap": 0.12},
            "g3",
            cached=False,
        ),
        _pair("US-2.1", "US-1.2", "fwd", {"unrelated": 0.95}, "g4", cached=False),
        _pair("US-1.2", "US-2.2", "fwd", None, "", cached=False),
        _atomic("US-1.1", 0.1),
        _atomic("US-1.2", 0.12),
        _atomic("US-2.1", 0.85),
    )
    verdicts = {
        ("US-1.1", "US-1.2"): _verdict("US-1.1", "US-1.2", Relation.DUPLICATE, 0.9876, 0.89),
        ("US-1.1", "US-2.1"): _verdict("US-1.1", "US-2.1", Relation.SAME_AREA, 0.1234, 0.0),
        ("US-1.2", "US-2.1"): _verdict("US-1.2", "US-2.1", Relation.UNRELATED, 0.0, 0.0),
    }
    run_meta: dict[str, Any] = {
        "run_id": "20260926-141200",
        "started_at": "2026-09-26T14:12:00+00:00",
        "finished_at": "2026-09-26T14:12:03+00:00",
        "wall_s": 3.2,
        "status": "complete",
        "snapshot_sha256": "b2dd2ef8cf7bd67fe151a1ca270f310efe3691fc58a41b1d7f1208a90611b14c",
        "tier": None,
        "epics": ["E1", "E2"],
        "n_stories": 4,
        "n_tombstones": 0,
        "n_pairs": 6,
        "render": "full",
        "jobs": {"fwd": 4, "swap": 1, "atomic": 3},
        "calls": {"live": 6, "cached": 1, "failed": 1, "retries": 5, "by_status": {"200": 6}},
        "tokens": {"total": 2884, "mean": 412.0, "p95": 412.0},
        "cost_usd": {
            "live": 1.04e-4,
            "cached_equivalent": 1.73e-5,
            "preflight_estimate": 1.3e-4,
            "key_usage_delta": 1.04e-4,
        },
        "latency_ms": {"p50": 301.0, "p95": 301.0},
        "calls_per_s": 2.1,
        "concurrency": 16,
        "rate_per_s": 50.0,
        "max_usd": 1.0,
        "model_requested": DEFAULT_MODEL,
        "models_resolved": [RESOLVED_MODEL],
        "mixed_models": False,
        "questions": {REL_V1.id: REL_V1.hash, ATOMIC_V2.id: ATOMIC_V2.hash},
        "prefilter": {
            "enabled": False,
            "k": 10,
            "top_pct": 2.0,
            "kept": 3,
            "recall_vs_jev_overlap": 1.0,
            "recall_vs_jev_merge": 1.0,
        },
        "thresholds": asdict(TH),
        "versions": {"tesselint": "0.1.0"},
    }
    return Planted(results, verdicts, run_meta)


def write(run_dir: Path) -> Planted:
    """Write the planted inputs to `run_dir` and return them."""
    inputs = planted()
    clusters = [Cluster("C1", ("US-1.1", "US-1.2"), 0.9876)]
    write_outputs(
        run_dir, STORIES, inputs.results, inputs.verdicts, clusters, [M1, S1], inputs.run_meta
    )
    return inputs


def test_matrix_is_square_in_document_order_with_blank_unasked_cells(tmp_path: Path) -> None:
    """matrix.csv: header `id,<ids>`, o to 3 decimals, symmetric, empty when not asked (§9)."""
    write(tmp_path)

    with (tmp_path / "matrix.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["id", *IDS]
    assert [row[0] for row in rows[1:]] == IDS
    assert all(len(row) == len(IDS) + 1 for row in rows)
    cells = {(a, b): rows[i + 1][j + 1] for i, a in enumerate(IDS) for j, b in enumerate(IDS)}
    expected = {(a, b): "" for a in IDS for b in IDS if a != b}
    for (a, b), value in MATRIX.items():
        expected[a, b] = expected[b, a] = value
    assert {pair: cell for pair, cell in cells.items() if pair[0] != pair[1]} == expected


def test_clusters_json_lists_clusters_edges_and_singletons(tmp_path: Path) -> None:
    """clusters.json: method, the run's cluster threshold, clusters with edges, singletons (§9)."""
    write(tmp_path)

    data = read_json(tmp_path / "clusters.json")

    assert (data["method"], data["threshold"]) == ("average_linkage", 0.6)
    assert data["singletons"] == ["US-2.1", "US-2.2"]
    [cluster] = data["clusters"]
    assert (cluster["id"], cluster["members"]) == ("C1", ["US-1.1", "US-1.2"])
    assert cluster["mean_overlap"] == pytest.approx(0.9876, abs=1e-3)
    edge = {
        "a": "US-1.1",
        "b": "US-1.2",
        "overlap": pytest.approx(0.9876, abs=1e-3),
        "merge": pytest.approx(0.89, abs=1e-3),
        "relation": "duplicate",
    }
    assert cluster["edges"] == [edge]


def test_pairs_jsonl_has_one_line_per_answer_used(tmp_path: Path) -> None:
    """pairs.jsonl: one rel.v1 line per answer, `a` as sent, failed jobs omitted (§9)."""
    inputs = write(tmp_path)

    lines = [row for row in read_jsonl(tmp_path / "pairs.jsonl") if row["question"] == REL_V1.id]

    answered = [r for r in inputs.results if r.answer and r.job.question.id == REL_V1.id]
    assert sorted(row["gen_id"] for row in lines) == ["g1", "g2", "g3", "g4"]
    by_gen = {row["gen_id"]: row for row in lines}
    for result in answered:
        assert result.answer is not None
        row, job, answer = by_gen[result.answer.gen_id], result.job, result.answer
        assert row.keys() >= PAIR_FIELDS
        assert (row["a"], row["b"], row["order"]) == (job.a, job.b, job.order)
        assert (row["text_hash_a"], row["text_hash_b"]) == (job.text_hash_a, job.text_hash_b)
        assert (row["question_hash"], row["cached"]) == (REL_V1.hash, result.cached)
        assert (row["choice"], row["model"]) == (answer.choice, answer.model)
        assert row["probabilities"] == pytest.approx(dict(answer.probabilities))
        assert row["confidence"] == pytest.approx(answer.confidence)
        assert row["input_tokens"] == answer.input_tokens
        assert row["cost_usd"] == pytest.approx(answer.cost_usd)
        assert row["latency_ms"] == pytest.approx(answer.latency_ms, abs=1)


def test_pairs_jsonl_writes_the_cosine_with_at_most_4_decimals(tmp_path: Path) -> None:
    """The cosine carries float noise that differs between platforms; pairs.jsonl rounds it."""
    inputs = planted()
    prefilter = {
        ("US-1.1", "US-1.2"): (0.20795007050037384, True),
        ("US-1.1", "US-2.1"): (0.123456789, False),
        ("US-1.2", "US-2.1"): (0.5, True),
    }
    clusters = [Cluster("C1", ("US-1.1", "US-1.2"), 0.9876)]
    write_outputs(
        tmp_path,
        STORIES,
        inputs.results,
        inputs.verdicts,
        clusters,
        [M1, S1],
        inputs.run_meta,
        prefilter=prefilter,
    )

    lines = (tmp_path / "pairs.jsonl").read_text("utf-8").splitlines()
    written = [re.search(r'"prefilter_cos": ([0-9.]+)', line) for line in lines]
    assert all(m is not None and len(m[1].split(".")[1]) <= 4 for m in written)
    by_pair = {
        tuple(sorted((row["a"], row["b"]))): row["prefilter_cos"]
        for row in read_jsonl(tmp_path / "pairs.jsonl")
    }
    assert by_pair == {
        ("US-1.1", "US-1.2"): 0.208,
        ("US-1.1", "US-2.1"): 0.1235,
        ("US-1.2", "US-2.1"): 0.5,
    }


def test_stories_jsonl_has_rendered_text_and_atomic_scores(tmp_path: Path) -> None:
    """stories.jsonl: one line per story, rendered text, atomic or skipped under 8 words (§9)."""
    write(tmp_path)

    rows = read_jsonl(tmp_path / "stories.jsonl")

    assert [row["id"] for row in rows] == IDS
    for row, story in zip(rows, STORIES, strict=True):
        text = render(story)
        assert row.keys() >= STORY_FIELDS
        assert (row["epic"], row["title"], row["tag"], row["line"]) == (
            story.epic,
            story.title,
            story.tag,
            story.line,
        )
        assert (row["text"], row["text_hash"]) == (text, text_hash(text))
        assert row["words"] == len(text.split())
    atomic = {row["id"]: (row["atomic"], row["atomic_skipped"]) for row in rows}
    assert atomic == {
        "US-1.1": (pytest.approx(0.1), False),
        "US-1.2": (pytest.approx(0.12), False),
        "US-2.1": (pytest.approx(0.85), False),
        "US-2.2": (None, True),
    }


def test_proposals_json_and_md_hold_every_proposal(tmp_path: Path) -> None:
    """proposals.json: thresholds and proposals in order; proposals.md names each one (§9)."""
    write(tmp_path)

    data = read_json(tmp_path / "proposals.json")
    markdown = (tmp_path / "proposals.md").read_text("utf-8")

    assert data["thresholds"] == asdict(TH)
    assert data["proposals"] == [
        {
            "id": "M1",
            "kind": "merge",
            "target": "US-1.1",
            "stories": ["US-1.1", "US-1.2"],
            "decisive_value": 0.89,
            "borderline": False,
            "evidence": list(M1.evidence),
        },
        {
            "id": "S1",
            "kind": "split",
            "target": None,
            "stories": ["US-2.1"],
            "decisive_value": 0.85,
            "borderline": False,
            "evidence": ["atomic 0.85"],
        },
    ]
    assert markdown.startswith("# tesselint proposals")
    assert "M1" in markdown and "S1" in markdown


def test_run_json_keeps_run_meta_and_counts_proposals(tmp_path: Path) -> None:
    """run.json: every run_meta field unchanged plus proposal counts by kind (§9)."""
    inputs = write(tmp_path)

    run = read_json(tmp_path / "run.json")

    assert run.keys() >= RUN_FIELDS
    assert {key: run[key] for key in inputs.run_meta} == inputs.run_meta
    counts = run["proposals"]
    assert (counts["merge"], counts["overlap"], counts["split"]) == (1, 0, 1)
