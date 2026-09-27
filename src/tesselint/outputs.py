"""Writers for the run directory files (DESIGN §9)."""

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tesselint.cluster import Cluster
from tesselint.config import DEFAULT_MODEL
from tesselint.evaluate import calibration_line
from tesselint.jobtypes import JobResult
from tesselint.models import Story, Thresholds
from tesselint.prefilter import rounded_cos
from tesselint.proposals import Proposal, render_proposals
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.render import render, text_hash
from tesselint.verdicts import PairVerdict


def write_outputs(
    run_dir: Path,
    stories: Sequence[Story],
    results: Sequence[JobResult],
    verdicts: Mapping[tuple[str, str], PairVerdict],
    clusters: Sequence[Cluster],
    proposals: Sequence[Proposal],
    run_meta: Mapping[str, Any],
    *,
    prefilter: Mapping[tuple[str, str], tuple[float, bool]] | None = None,
) -> None:
    """Write stories.jsonl, pairs.jsonl, matrix.csv, clusters.json, proposals.md/.json, run.json.

    `prefilter` maps an id pair (either order) to (cosine, kept) for pairs.jsonl (§9). The
    proposals.md header takes run_meta's `models_resolved` and optional `calibration` line.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    render_mode = run_meta.get("render", "full")

    th = _thresholds(run_meta.get("thresholds"))

    # 1. stories.jsonl (atomic_skipped: never asked, as opposed to asked and failed)
    atomic_asked: set[str] = set()
    atomic_scores: dict[str, float] = {}
    for r in results:
        if r.job.question.id == ATOMIC_V2.id:
            atomic_asked.add(r.job.a)
            yes_prob = r.answer.probabilities.get("yes") if r.answer is not None else None
            if yes_prob is not None:
                atomic_scores[r.job.a] = yes_prob

    stories_rows: list[dict[str, Any]] = []
    for s in stories:
        text = render(s, render_mode)
        stories_rows.append(
            {
                "id": s.id,
                "epic": s.epic,
                "title": s.title,
                "tag": s.tag,
                "line": s.line,
                "words": len(text.split()),
                "text_hash": text_hash(text),
                "text": text,
                "atomic": atomic_scores.get(s.id),
                "atomic_skipped": s.id not in atomic_asked,
            }
        )
    (run_dir / "stories.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in stories_rows), encoding="utf-8"
    )

    # 2. pairs.jsonl (one rel.v1 line per answer used, failed jobs omitted)
    pairs_rows: list[dict[str, Any]] = []
    for r in results:
        if r.job.question.id == REL_V1.id and r.answer is not None and r.job.b is not None:
            found = None
            if prefilter:  # keyed in document or string order: look both ways
                found = prefilter.get((r.job.a, r.job.b)) or prefilter.get((r.job.b, r.job.a))
            cos, kept = found if found is not None else (None, True)

            pairs_rows.append(
                {
                    "a": r.job.a,
                    "b": r.job.b,
                    "order": r.job.order,
                    "question": r.job.question.id,
                    "question_hash": r.job.question.hash,
                    "text_hash_a": r.job.text_hash_a,
                    "text_hash_b": r.job.text_hash_b,
                    "choice": r.answer.choice,
                    "confidence": r.answer.confidence,
                    "probabilities": dict(r.answer.probabilities),
                    "model": r.answer.model,
                    "input_tokens": r.answer.input_tokens,
                    "cost_usd": r.answer.cost_usd,
                    "latency_ms": r.answer.latency_ms,
                    "cached": r.cached,
                    "gen_id": r.answer.gen_id,
                    "prefilter_cos": None if cos is None else rounded_cos(cos),
                    "prefilter_kept": kept,
                }
            )
    (run_dir / "pairs.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in pairs_rows), encoding="utf-8"
    )

    # 3. matrix.csv (square in document order, blank for unasked / diagonal)
    ids = [s.id for s in stories]
    matrix_rows: list[list[str]] = [["id", *ids]]
    for sid_a in ids:
        row = [sid_a]
        for sid_b in ids:
            if sid_a == sid_b:
                row.append("")
            else:
                v = verdicts.get((sid_a, sid_b)) or verdicts.get((sid_b, sid_a))
                if v is not None:
                    row.append(f"{v.overlap:.3f}")
                else:
                    row.append("")
        matrix_rows.append(row)

    with (run_dir / "matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(matrix_rows)

    # 4. clusters.json
    clustered_ids: set[str] = set()
    clusters_list: list[dict[str, Any]] = []

    for c in clusters:
        clustered_ids.update(c.members)
        edges: list[dict[str, Any]] = []
        for i, a in enumerate(c.members):
            for b in c.members[i + 1 :]:
                v = verdicts.get((a, b)) or verdicts.get((b, a))
                if v is not None:
                    edges.append(
                        {
                            "a": v.a,
                            "b": v.b,
                            "overlap": v.overlap,
                            "merge": v.merge,
                            "relation": v.relation.value,
                        }
                    )
        edges.sort(key=lambda e: (e["a"], e["b"]))
        clusters_list.append(
            {
                "id": c.id,
                "members": list(c.members),
                "mean_overlap": c.mean_overlap,
                "edges": edges,
            }
        )

    singletons = [s.id for s in stories if s.id not in clustered_ids]
    clusters_data = {
        "method": "average_linkage",
        "threshold": th.cluster,
        "clusters": clusters_list,
        "singletons": singletons,
    }
    (run_dir / "clusters.json").write_text(json.dumps(clusters_data, indent=2), encoding="utf-8")

    # 5. proposals.json & proposals.md
    write_proposals(run_dir, stories, proposals, run_meta, th)

    # 6. run.json (run_meta with proposal counts by kind)
    counts = Counter(p.kind.value for p in proposals)
    run_dict = dict(run_meta)
    run_dict["proposals"] = {
        "merge": counts["merge"],
        "overlap": counts["overlap"],
        "split": counts["split"],
        "leave": counts["leave"],
    }
    (run_dir / "run.json").write_text(json.dumps(run_dict, indent=2), encoding="utf-8")


def write_proposals(
    run_dir: Path,
    stories: Sequence[Story],
    proposals: Sequence[Proposal],
    run_meta: Mapping[str, Any],
    th: Thresholds,
) -> None:
    """Write proposals.json (`proposals_json`) and proposals.md; `run` and `report` share it."""
    body = proposals_json(proposals, th)
    (run_dir / "proposals.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    md_content = render_proposals(proposals, stories, proposals_header(run_dir, run_meta), th=th)
    (run_dir / "proposals.md").write_text(md_content, encoding="utf-8")


def proposals_json(proposals: Sequence[Proposal], th: Thresholds) -> dict[str, Any]:
    """proposals.json: the thresholds and one row per proposal, in order (DESIGN §9).

    A keyed proposal (every one `propose()` makes) adds `key` and `details` to the §9 fields
    (docs/ui/DESIGN.md §3, §4.3, §7.3). The hubs are not written here: `proposals.json` keeps its
    two top-level keys (tests/test_pipeline.py) and the server derives them with `hub_rows`.
    """
    return {"thresholds": asdict(th), "proposals": [_proposal_row(p) for p in proposals]}


def _proposal_row(p: Proposal) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": p.id,
        "kind": p.kind.value,
        "target": p.target,
        "stories": list(p.stories),
        "decisive_value": p.decisive_value,
        "borderline": p.borderline,
        "evidence": list(p.evidence),
    }
    if p.key:
        row |= {"key": p.key, "details": p.details}
    return row


def _thresholds(value: object) -> Thresholds:
    """run_meta["thresholds"] as Thresholds, whether given as the dataclass or as its dict."""
    if isinstance(value, Thresholds):
        return value
    if isinstance(value, Mapping):
        return Thresholds(**value)
    return Thresholds()


def proposals_header(run_dir: Path, run_meta: Mapping[str, Any]) -> dict[str, str]:
    """proposals.md header: run id, snapshot, resolved model(s) and the calibration line.

    The calibration line is run_meta["calibration"], else the one of the run's calibration.json
    (written by `eval --labels`), else absent (render_proposals prints "not run").
    """
    models = run_meta.get("models_resolved") or [run_meta.get("model_requested", DEFAULT_MODEL)]
    header = {
        "run_id": str(run_meta.get("run_id", "")),
        "snapshot_sha256": str(run_meta.get("snapshot_sha256", "")),
        "model": ", ".join(str(m) for m in models),
    }
    calibration = run_meta.get("calibration")
    calibration_json = run_dir / "calibration.json"
    if not isinstance(calibration, str) and calibration_json.is_file():
        try:
            scores = json.loads(calibration_json.read_text(encoding="utf-8"))
            calibration = calibration_line(scores if isinstance(scores, dict) else {})
        except (OSError, ValueError):
            calibration = "calibration.json is unreadable; re-run tesselint eval RUN --labels"
    if isinstance(calibration, str):
        header["calibration"] = calibration
    return header
