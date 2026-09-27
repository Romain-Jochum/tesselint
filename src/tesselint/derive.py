"""Read-only re-derivation of a run's proposals: `report` without writes (docs/ui/DESIGN.md §4.3).

The server imports this module, so it must never load `jev`, `workers`, `httpx` or `dotenv`
(§1): the job types come from `jobtypes`.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tesselint.cluster import Cluster, average_linkage
from tesselint.errors import InputError
from tesselint.jobtypes import Answer, Job, JobResult
from tesselint.models import Story, Thresholds
from tesselint.parse import load_snapshot, parse_snapshot, select_epics
from tesselint.proposals import Proposal, propose
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.verdicts import PairVerdict, build_verdicts, overlap_matrix

RUN_FILES = ("run.json", "stories.jsonl", "pairs.jsonl")  # what derive reads
SNAPSHOTS = ("snapshot.json", "snapshot.md")  # a run holds one (docs/ui/DESIGN.md §4.5)


@dataclass(frozen=True, slots=True)
class Derived:
    """Everything `report` computes for a run, in document order (docs/ui/DESIGN.md §7.1)."""

    stories: tuple[Story, ...]
    verdicts: Mapping[tuple[str, str], PairVerdict]
    clusters: tuple[Cluster, ...]
    atomic: Mapping[str, float | None]
    proposals: tuple[Proposal, ...]
    thresholds: Thresholds


def derive(run_dir: Path, th: Thresholds) -> Derived:
    """Rebuild verdicts, clusters and proposals of a run with `th`; writes nothing (§4.3).

    Only the run's own stories (its `epics` in run.json) are re-derived, from its snapshot when it
    has one, else from stories.jsonl. A directory that is not a run, or unreadable run files,
    raise InputError.
    """
    missing = [name for name in RUN_FILES if not (run_dir / name).is_file()]
    if missing:
        raise InputError(f"{run_dir}: not a run directory (missing {', '.join(missing)})")
    try:
        meta = json.loads((run_dir / "run.json").read_text("utf-8"))
        story_rows = read_jsonl(run_dir / "stories.jsonl")
        pair_rows = read_jsonl(run_dir / "pairs.jsonl")
        results = [_result_from_row(row) for row in pair_rows]
        stories = _run_stories(run_dir, meta, story_rows)
        prefilter = {
            (row["a"], row["b"]): (float(row["prefilter_cos"]), bool(row.get("prefilter_kept")))
            for row in pair_rows
            if row.get("b") is not None and row.get("prefilter_cos") is not None
        }
        atomic: dict[str, float | None] = {row["id"]: row.get("atomic") for row in story_rows}
    except (OSError, KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
        raise InputError(f"{run_dir}: unreadable run files ({type(e).__name__}: {e})") from e

    verdicts = build_verdicts(stories, results)
    ids = [s.id for s in stories]
    clusters = average_linkage(ids, overlap_matrix(ids, verdicts), threshold=th.cluster)
    proposals = propose(stories, verdicts, clusters, atomic, th, prefilter=prefilter)
    return Derived(stories, verdicts, tuple(clusters), atomic, tuple(proposals), th)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSON-lines file, skipping blank lines."""
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _run_stories(
    run_dir: Path, meta: Mapping[str, Any], story_rows: Sequence[Mapping[str, Any]]
) -> tuple[Story, ...]:
    """The run's stories: its snapshot re-parsed and cut to its epics, else from stories.jsonl."""
    if not any((run_dir / name).is_file() for name in SNAPSHOTS):
        return tuple(
            Story(r["id"], r["epic"], r["title"], r["tag"], r["text"], (), r["line"])
            for r in story_rows
        )
    parsed = parse_snapshot(load_snapshot(run_dir))
    epics = meta.get("epics")
    stories = select_epics(parsed.stories, epics) if epics else parsed.stories
    if [s.id for s in stories] != [r["id"] for r in story_rows]:
        raise ValueError("the snapshot's stories for the run's epics differ from stories.jsonl")
    return stories


def _result_from_row(row: Mapping[str, Any]) -> JobResult:
    """A JobResult rebuilt from one pairs.jsonl line (texts are not needed, so hashes stand in)."""
    b = row.get("b")
    job = Job(
        key=f"{row['question']}:{row['order']}:{row['a']}:{b}",
        a=row["a"],
        b=b,
        order=row["order"],
        state={"A": row["text_hash_a"], "B": row["text_hash_b"]} if b else row["text_hash_a"],
        question=REL_V1 if row["question"] == REL_V1.id else ATOMIC_V2,
        est_tokens=row.get("input_tokens", 0),
        text_hash_a=row["text_hash_a"],
        text_hash_b=row.get("text_hash_b"),
    )
    answer = Answer(
        question_id=row["question"],
        choice=row.get("choice"),
        confidence=row.get("confidence"),
        probabilities=row["probabilities"],
        model=row.get("model", ""),
        input_tokens=row.get("input_tokens", 0),
        cost_usd=row.get("cost_usd", 0.0),
        gen_id=row.get("gen_id", ""),
        latency_ms=row.get("latency_ms", 0.0),
    )
    return JobResult(
        job=job, answer=answer, cached=row.get("cached", False), attempts=1, error=None
    )
