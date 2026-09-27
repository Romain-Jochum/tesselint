"""Regressions from the analysis-layer review (verdicts, proposals, outputs, evaluate, synth).

Each test names the review item it pins (H1-H4, M1-M8, L1-L3). Inputs are planted by hand and the
expected numbers are worked out in the comments.
"""

import json
import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from importlib.resources import files
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from tesselint.cluster import Cluster
from tesselint.errors import InputError, UsageError
from tesselint.evaluate import (
    calibration_line,
    load_truth,
    make_label_sheet,
    score_labels,
    score_synthetic,
)
from tesselint.jev import Answer
from tesselint.models import Order, ProposalKind, Relation, Story, Thresholds
from tesselint.outputs import write_outputs
from tesselint.parse import parse_section, take_snapshot
from tesselint.proposals import Proposal, propose, render_proposals
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.render import render, text_hash
from tesselint.synth import Truth, generate, truth_to_dict
from tesselint.verdicts import PairVerdict, build_verdicts
from tesselint.workers import Job, JobResult

R = Relation
K = ProposalKind
TH = Thresholds()
MODEL = "typesafe/jev-1.13-20260917"
LONG_CORE = (
    "I record every plot inspection on my site with photos and notes, "
    "so that the committee can follow up on problems."
)
SHA = "b2dd2ef8cf7bd67fe151a1ca270f310efe3691fc58a41b1d7f1208a90611b14c"


# ---------------------------------------------------------------- helpers


def _story(
    sid: str,
    epic: str = "E1",
    *,
    title: str = "",
    core: str = LONG_CORE,
    subs: tuple[str, ...] = ("Each record names the plot.",),
    tag: str = "MVP",
) -> Story:
    title = title or f"Inspection record {sid.removeprefix('US-')}"  # no accidental twins
    return Story(id=sid, epic=epic, title=title, tag=tag, core=core, subs=subs, line=0)


def _pv(
    a: str,
    b: str,
    fwd: Mapping[str, float],
    swap: Mapping[str, float] | None = None,
) -> PairVerdict:
    """A verdict in document order; `fwd`/`swap` are keyed by Relation values, doc-ordered."""
    f = {Relation(k): v for k, v in fwd.items()}
    s = None if swap is None else {Relation(k): v for k, v in swap.items()}
    probs = f if s is None else {r: (f.get(r, 0.0) + s.get(r, 0.0)) / 2 for r in Relation}
    merge = probs.get(R.DUPLICATE, 0.0) + probs.get(R.A_CONTAINS_B, 0.0)
    merge += probs.get(R.B_CONTAINS_A, 0.0)
    overlap = merge + probs.get(R.PARTIAL, 0.0)
    relation = max(probs, key=lambda r: probs[r])
    return PairVerdict(a, b, probs, f, s, overlap, merge, relation)


def _index(verdicts: Iterable[PairVerdict]) -> dict[tuple[str, str], PairVerdict]:
    return {(v.a, v.b): v for v in verdicts}


def _of(proposals: Sequence[Proposal], kind: ProposalKind) -> list[Proposal]:
    return [p for p in proposals if p.kind == kind]


def _mentions(text: str, sid: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(sid)}(?!\d)", text) is not None


def _rel(sent_a: str, sent_b: str, order: Order, probs: Mapping[str, float]) -> JobResult:
    job = Job(
        key=f"{order}:{sent_a}:{sent_b}",
        a=sent_a,
        b=sent_b,
        order=order,
        state={"A": sent_a, "B": sent_b},
        question=REL_V1,
        est_tokens=600,
        text_hash_a=f"hash-{sent_a}",
        text_hash_b=f"hash-{sent_b}",
    )
    answer = Answer(REL_V1.id, None, None, dict(probs), MODEL, 600, 2.5e-5, "gen", 300.0)
    return JobResult(job=job, answer=answer, cached=False, attempts=1, error=None)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _pair_row(
    a: str, b: str, order: str, probs: Mapping[str, float], *, kept: bool = True
) -> dict[str, Any]:
    return {
        "a": a,
        "b": b,
        "order": order,
        "question": "rel.v1",
        "question_hash": "qhash",
        "text_hash_a": f"hash-{a}",
        "text_hash_b": f"hash-{b}",
        "choice": max(probs, key=lambda k: probs[k]),
        "confidence": None,
        "probabilities": dict(probs),
        "model": MODEL,
        "input_tokens": 600,
        "cost_usd": 2.5e-5,
        "latency_ms": 300,
        "cached": False,
        "gen_id": "gen",
        "prefilter_cos": 0.3,
        "prefilter_kept": kept,
    }


def _write_run(
    run_dir: Path,
    ids: Sequence[str],
    pair_rows: Sequence[Mapping[str, Any]],
    proposals: Sequence[Mapping[str, Any]] = (),
    atomic: Mapping[str, float] | None = None,
) -> Path:
    """A run dir with stories.jsonl (in `ids` order), pairs.jsonl and proposals.json."""
    run_dir.mkdir(parents=True)
    scores = atomic or {}
    _write_jsonl(
        run_dir / "stories.jsonl",
        (
            {
                "id": sid,
                "epic": "E" + sid.removeprefix("US-").split(".")[0],
                "title": f"Story {i}",
                "tag": "MVP",
                "line": i + 1,
                "words": 14,
                "text_hash": f"hash-{sid}",
                "text": f"Story {sid}. I tend my plot, so that it grows well.",
                "atomic": scores.get(sid),
                "atomic_skipped": sid not in scores,
            }
            for i, sid in enumerate(ids)
        ),
    )
    _write_jsonl(run_dir / "pairs.jsonl", pair_rows)
    body = {"thresholds": asdict(TH), "proposals": list(proposals)}
    (run_dir / "proposals.json").write_text(json.dumps(body), encoding="utf-8")
    return run_dir


def _prop(pid: str, kind: str, stories: list[str], target: str | None = None) -> dict[str, Any]:
    return {
        "id": pid,
        "kind": kind,
        "target": target,
        "stories": stories,
        "decisive_value": 0.9,
        "borderline": False,
        "evidence": [],
    }


# ---------------------------------------------------------------- H1 pair lookups


def test_h1_split_corroboration_finds_edges_keyed_in_document_order() -> None:
    """H1: US-1.2 precedes US-1.11, so its edges are keyed (US-1.2, US-1.11), not by string order.

    atomic 0.75 >= 0.70 with o 0.55 edges into C1 and C2 -> corroborated split, borderline.
    """
    ids = ["US-1.2", "US-1.11", "US-1.12", "US-1.13", "US-1.14"]
    stories = [_story(sid) for sid in ids]
    edge = {"partial_overlap": 0.55, "same_area_distinct": 0.45}
    verdicts = _index(
        [
            _pv("US-1.11", "US-1.12", {"partial_overlap": 0.9, "unrelated": 0.1}),
            _pv("US-1.13", "US-1.14", {"partial_overlap": 0.9, "unrelated": 0.1}),
            _pv("US-1.2", "US-1.11", edge),
            _pv("US-1.2", "US-1.13", edge),
        ]
    )
    clusters = [
        Cluster("C1", ("US-1.11", "US-1.12"), 0.9),
        Cluster("C2", ("US-1.13", "US-1.14"), 0.9),
    ]
    atomic: dict[str, float | None] = {sid: 0.1 for sid in ids}
    atomic["US-1.2"] = 0.75

    splits = _of(propose(stories, verdicts, clusters, atomic, TH), K.SPLIT)

    assert [(p.stories, p.decisive_value, p.borderline) for p in splits] == [
        (("US-1.2",), 0.75, True)
    ]
    assert any("overlaps C1, C2" in e for e in splits[0].evidence)


def test_h1_also_related_finds_pairs_keyed_in_document_order() -> None:
    """H1: the merge US-3.1 -> US-1.2 lists US-1.11 (o 0.45 with US-1.2, keyed US-1.2 first)."""
    stories = [_story("US-1.2"), _story("US-1.11"), _story("US-3.1", "E3")]
    verdicts = _index(
        [
            _pv("US-1.2", "US-3.1", {"duplicate": 0.9, "unrelated": 0.1}),
            _pv("US-1.2", "US-1.11", {"partial_overlap": 0.45, "same_area_distinct": 0.55}),
            _pv("US-1.11", "US-3.1", {"unrelated": 1.0}),
        ]
    )

    [merge] = _of(propose(stories, verdicts, [], {}, TH), K.MERGE)

    related = [e for e in merge.evidence if e.startswith("also related")]
    assert related == ["also related: US-1.2 ~ US-1.11 (o 0.45)"]


# ---------------------------------------------------------------- H2 identical texts

TWIN_TITLE = "Water rota"
TWIN_CORE = "I see the watering rota for my site, so that I help."


def test_h2_identical_texts_get_a_local_duplicate_verdict_and_merge() -> None:
    """H2 / §2.1: identical texts get duplicate = 1.0 with no call; the later merges into the
    earlier. Non-identical pairs that were never asked stay without a verdict."""
    stories = [
        _story("US-1.1", title=TWIN_TITLE, core=TWIN_CORE),
        _story("US-1.2"),
        _story("US-2.5", "E2", title=TWIN_TITLE, core=TWIN_CORE),
    ]

    verdicts = build_verdicts(stories, [])

    assert set(verdicts) == {("US-1.1", "US-2.5")}
    v = verdicts[("US-1.1", "US-2.5")]
    assert (v.overlap, v.merge, v.relation, v.swap) == (1.0, 1.0, R.DUPLICATE, None)
    assert v.probs[R.DUPLICATE] == 1.0
    [merge] = _of(propose(stories, verdicts, [], {}, TH), K.MERGE)
    assert (merge.target, merge.stories) == ("US-1.1", ("US-1.1", "US-2.5"))
    assert merge.evidence[0] == "duplicate"


def test_h2_an_answer_for_identical_texts_is_not_overwritten() -> None:
    """H2: a verdict built from an answer (e.g. the pipeline's local one) wins over the fallback."""
    stories = [
        _story("US-1.1", title=TWIN_TITLE, core=TWIN_CORE),
        _story("US-1.2", title=TWIN_TITLE, core=TWIN_CORE),
    ]

    verdicts = build_verdicts(stories, [_rel("US-1.1", "US-1.2", "fwd", {"duplicate": 0.8})])

    assert verdicts[("US-1.1", "US-1.2")].merge == pytest.approx(0.8)


# ---------------------------------------------------------------- H3 evidence

EVIDENCE_STORIES = [
    _story("US-1.1", "E2", title="Sign up with email", subs=("One.", "Two.")),
    _story("US-20.1", "E20", title="Profile page"),
    _story("US-20.2", "E20", title="Register by email"),
    _story("US-9.1", "E9", title="Compost bins"),
]


def _evidence_run() -> list[Proposal]:
    """US-1.1 contains US-20.2: fwd A>B .54/partial .36, swap A>B .66/partial .30.

    Averages: A>B .60, partial .33, same-area .07 -> m 0.60, o 0.93. cos 0.61 is keyed in reverse
    order on purpose. US-20.1 is related to US-1.1 at o 0.45; US-9.1 at 0.35 is not.
    """
    verdicts = _index(
        [
            _pv(
                "US-1.1",
                "US-20.2",
                {"A_contains_B": 0.54, "partial_overlap": 0.36, "same_area_distinct": 0.10},
                {"A_contains_B": 0.66, "partial_overlap": 0.30, "same_area_distinct": 0.04},
            ),
            _pv("US-1.1", "US-20.1", {"partial_overlap": 0.45, "same_area_distinct": 0.55}),
            _pv("US-1.1", "US-9.1", {"partial_overlap": 0.35, "same_area_distinct": 0.65}),
            _pv("US-20.1", "US-20.2", {"same_area_distinct": 1.0}),
        ]
    )
    clusters = [Cluster("C1", ("US-1.1", "US-20.2"), 0.93)]
    atomic: dict[str, float | None] = {"US-1.1": 0.12, "US-20.1": 0.2, "US-20.2": 0.3}
    prefilter = {("US-20.2", "US-1.1"): (0.61, True)}
    return propose(EVIDENCE_STORIES, verdicts, clusters, atomic, TH, prefilter=prefilter)


def test_h3_merge_evidence_has_relation_titles_probabilities_cluster_and_cosine() -> None:
    """H3 / §2.5: relation, titles, top-2 per order, o, cluster, atomicity, cosine, related."""
    [merge] = _of(_evidence_run(), K.MERGE)

    assert merge.decisive_value == pytest.approx(0.60)
    assert merge.evidence == (
        "containment",
        'keep US-1.1 "Sign up with email" (bundled 0.12) · '
        'drop US-20.2 "Register by email" (bundled 0.30)',
        "fwd A⊃B 0.54 / partial 0.36 · swap A⊃B 0.66 / partial 0.30 · o 0.93 · C1 · cos 0.61",
        "also related: US-1.1 ~ US-20.1 (o 0.45)",
    )


def test_h3_overlap_evidence_lists_members_edges_and_related() -> None:
    """H3: an overlap shows member titles, its edges by o (desc) and outside stories at o >= 0.4."""
    stories = [
        _story("US-2.1", "E2", title="Lend a tool"),
        _story("US-2.2", "E2", title="Return a tool"),
        _story("US-2.3", "E2", title="Report a broken tool"),
        _story("US-4.1", "E4", title="Swap seeds"),
    ]
    verdicts = _index(
        [
            _pv("US-2.1", "US-2.2", {"partial_overlap": 0.7, "unrelated": 0.3}),
            _pv("US-2.1", "US-2.3", {"partial_overlap": 0.5, "unrelated": 0.5}),
            _pv("US-2.2", "US-2.3", {"partial_overlap": 0.6, "unrelated": 0.4}),
            _pv("US-2.3", "US-4.1", {"partial_overlap": 0.42, "unrelated": 0.58}),
        ]
    )
    clusters = [Cluster("C1", ("US-2.1", "US-2.2", "US-2.3"), 0.6)]

    [overlap] = _of(propose(stories, verdicts, clusters, {}, TH), K.OVERLAP)

    assert overlap.evidence == (
        'US-2.1 "Lend a tool" · US-2.2 "Return a tool" · US-2.3 "Report a broken tool"',
        "edges: US-2.1 ~ US-2.2 (o 0.70) · US-2.2 ~ US-2.3 (o 0.60) · US-2.1 ~ US-2.3 (o 0.50)",
        "also related: US-2.3 ~ US-4.1 (o 0.42)",
    )


def test_h3_split_evidence_has_score_words_subs_clusters_and_tag() -> None:
    """H3: a split shows words (30, full text, "-" counts), sub-bullets, reached clusters, tag;
    its bundled score is the decisive value in the heading."""
    bundle = _story(
        "US-3.3",
        "E3",
        title="Plan and pay",
        core="I plan my beds and pay my rent online, so that both are done.",
        subs=("Beds are drawn on a grid.", "Rent is paid by card."),
        tag="MVP; the brief Lite",
    )
    assert len(render(bundle).split()) == 30  # 3 + 14 + 7 + 6
    stories = [_story("US-1.1"), _story("US-1.2"), _story("US-2.1"), _story("US-2.2"), bundle]
    edge = {"partial_overlap": 0.5, "same_area_distinct": 0.5}
    verdicts = _index(
        [
            _pv("US-1.1", "US-1.2", {"partial_overlap": 0.8, "unrelated": 0.2}),
            _pv("US-2.1", "US-2.2", {"partial_overlap": 0.8, "unrelated": 0.2}),
            _pv("US-1.1", "US-3.3", edge),
            _pv("US-2.2", "US-3.3", edge),
        ]
    )
    clusters = [Cluster("C1", ("US-1.1", "US-1.2"), 0.8), Cluster("C2", ("US-2.1", "US-2.2"), 0.8)]

    [split] = _of(propose(stories, verdicts, clusters, {"US-3.3": 0.9}, TH), K.SPLIT)

    assert split.evidence == (
        'US-3.3 "Plan and pay"',
        "30 words · 2 sub-bullets · overlaps C1, C2 · tag [MVP; the brief Lite]",
    )


# ---------------------------------------------------------------- H4 contradictions


def test_h4_conflicting_merges_and_splits_are_marked() -> None:
    """H4: US-1.3 is contained by US-1.4 (m .8), US-1.1 (.7) and US-1.2 (.6) and bundled (.9).

    Merges sort by m, so M1 keeps US-1.4. M2 and M3 conflict with M1; every merge conflicts with
    the split S1 of the same story, and S1 conflicts with all three.
    """
    stories = [_story(f"US-1.{k}") for k in range(1, 5)]
    verdicts = _index(
        [
            _pv(
                "US-1.1", "US-1.3", {"A_contains_B": 0.7, "partial_overlap": 0.2, "unrelated": 0.1}
            ),
            _pv(
                "US-1.2", "US-1.3", {"A_contains_B": 0.6, "partial_overlap": 0.3, "unrelated": 0.1}
            ),
            _pv("US-1.3", "US-1.4", {"B_contains_A": 0.8, "unrelated": 0.2}),
            _pv("US-1.1", "US-1.2", {"partial_overlap": 0.6, "unrelated": 0.4}),
        ]
    )
    atomic: dict[str, float | None] = {"US-1.1": 0.1, "US-1.2": 0.1, "US-1.3": 0.9, "US-1.4": 0.1}

    proposals = propose(stories, verdicts, [], atomic, TH)

    merges = _of(proposals, K.MERGE)
    assert [(p.id, p.target) for p in merges] == [
        ("M1", "US-1.4"),
        ("M2", "US-1.1"),
        ("M3", "US-1.2"),
    ]
    assert [p.decisive_value for p in merges] == pytest.approx([0.8, 0.7, 0.6])
    conflicts = {p.id: [e for e in p.evidence if e.startswith("⚠")] for p in proposals}
    assert conflicts["M1"] == ["⚠ conflicts with S1"]
    assert conflicts["M2"] == ["⚠ conflicts with M1, S1"]
    assert conflicts["M3"] == ["⚠ conflicts with M1, S1"]
    assert conflicts["S1"] == ["⚠ conflicts with M1, M2, M3"]


def test_h4_independent_proposals_carry_no_conflict() -> None:
    """H4: a lone merge with no split on its stories is not marked."""
    proposals = _evidence_run()

    assert not any(e.startswith("⚠") for p in proposals for e in p.evidence)


# ---------------------------------------------------------------- M1 epsilon


def test_m1_merge_and_overlap_sums_at_half_are_not_lost_to_float_error() -> None:
    """M1: 0.15 + 0.30 + 0.05 is 0.49999999999999994 in floats; m and o must read 0.5."""
    stories = [_story("US-1.1"), _story("US-1.2"), _story("US-1.3")]
    results = [
        _rel(
            "US-1.1",
            "US-1.2",
            "fwd",
            {
                "duplicate": 0.15,
                "A_contains_B": 0.30,
                "B_contains_A": 0.05,
                "partial_overlap": 0.2,
                "unrelated": 0.3,
            },
        ),
        _rel(
            "US-1.1",
            "US-1.3",
            "fwd",
            {"A_contains_B": 0.15, "B_contains_A": 0.3, "partial_overlap": 0.05, "unrelated": 0.5},
        ),
    ]

    verdicts = build_verdicts(stories, results)

    assert verdicts[("US-1.1", "US-1.2")].merge >= 0.5
    assert verdicts[("US-1.1", "US-1.3")].overlap >= 0.5
    merges = _of(propose(stories, verdicts, [], {}, TH), K.MERGE)
    assert [p.stories for p in merges] == [("US-1.1", "US-1.2")]


# ---------------------------------------------------------------- M8 proposals.md


def _md_proposals() -> list[Proposal]:
    return [
        Proposal("M2", K.MERGE, "US-20.6", ("US-20.6", "US-2.1"), 0.53, True, ("duplicate",)),
        Proposal(
            "M1",
            K.MERGE,
            "US-1.1",
            ("US-1.1", "US-20.2"),
            0.8,
            False,
            ("containment", 'keep US-1.1 "Sign up" · drop US-20.2 "Register"'),
        ),
        Proposal("O1", K.OVERLAP, "C2", ("US-1.2", "US-1.6"), 0.61, False, ()),
        Proposal("O2", K.OVERLAP, "C1", ("US-2.2", "US-2.3"), 0.89, False, ()),
        Proposal("S1", K.SPLIT, None, ("US-3.3",), 0.85, True, ("tag [MVP]",)),
        Proposal("L1", K.LEAVE, None, ("US-1.3",), 0.0, False, ()),
    ]


MD_STORIES = [
    _story(sid, "E" + sid.removeprefix("US-").split(".")[0])
    for sid in (
        "US-1.1",
        "US-1.2",
        "US-1.3",
        "US-1.6",
        "US-2.1",
        "US-2.2",
        "US-2.3",
        "US-20.2",
        "US-20.6",
        "US-3.3",
    )
]


def test_m8_markdown_has_separate_sections_sorted_by_decisive_value() -> None:
    """M8: Merge, Overlap, Split, Leave alone; each sorted by decisive value, highest first."""
    header = {"run_id": "20260926-141229", "snapshot_sha256": SHA, "model": MODEL}

    lines = render_proposals(_md_proposals(), MD_STORIES, header).splitlines()

    sections = [line for line in lines if line.startswith("## ")]
    assert sections == ["## Merge", "## Overlap", "## Split", "## Leave alone (1)"]
    headings = [line for line in lines if line.startswith("### ")]
    assert headings == [
        "### M1 · merge US-20.2 → US-1.1 (containment) · m 0.80",
        "### M2 · merge US-2.1 → US-20.6 (duplicate) · m 0.53 · borderline",
        "### O2 · overlap C1: US-2.2, US-2.3 · mean o 0.89",
        "### O1 · overlap C2: US-1.2, US-1.6 · mean o 0.61",
        "### S1 · split US-3.3 · bundled 0.85 · borderline",
    ]
    assert "- containment" not in lines  # the relation goes in the heading, not a bullet
    assert '- keep US-1.1 "Sign up" · drop US-20.2 "Register"' in lines


def test_m8_header_has_short_hash_model_and_default_calibration() -> None:
    """M8: run id, short snapshot hash, resolved model; calibration defaults to "not run"."""
    header = {"run_id": "20260926-141229", "snapshot_sha256": SHA, "model": MODEL}

    lines = render_proposals(_md_proposals(), MD_STORIES, header).splitlines()

    assert lines[0] == f"# tesselint proposals · run 20260926-141229 · snapshot b2dd2ef8… · {MODEL}"
    assert lines[1] == "Calibration: not run (tesselint eval RUN --make-labels)"
    notes = [line for line in lines if line.startswith("How to read this")]
    assert len(notes) == 1
    assert "0.50" in notes[0] and "0.80" in notes[0] and "borderline" in notes[0].lower()


def test_m8_calibration_failure_puts_a_not_trusted_banner_on_its_sections() -> None:
    """M8 / §5: the header's calibration line is shown, and a failed question flags its sections."""
    header = {
        "run_id": "r",
        "snapshot_sha256": SHA,
        "model": MODEL,
        "calibration": "rel.v1 PASS · atomic.v2 FAIL (not trusted)",
    }

    text = render_proposals(_md_proposals(), MD_STORIES, header)

    lines = text.splitlines()
    assert lines[1] == "Calibration: rel.v1 PASS · atomic.v2 FAIL (not trusted)"
    split_at = lines.index("## Split")
    assert "not trusted" in " ".join(lines[split_at : split_at + 3]).lower()
    merge_at = lines.index("## Merge")
    assert "not trusted" not in " ".join(lines[merge_at : merge_at + 3]).lower()


def test_m8_also_related_is_sorted_by_overlap_and_capped_at_five() -> None:
    """M8: 8 related stories (o .41-.48) -> the 5 highest in order, then "+3 more"."""
    others = [f"US-2.{k}" for k in range(1, 9)]
    stories = [_story("US-1.1"), _story("US-1.2"), *(_story(sid, "E2") for sid in others)]
    verdicts = [_pv("US-1.1", "US-1.2", {"duplicate": 0.9, "unrelated": 0.1})]
    for k, sid in enumerate(others, start=1):
        o = 0.40 + k / 100
        verdicts.append(_pv("US-1.2", sid, {"partial_overlap": o, "unrelated": 1 - o}))

    [merge] = _of(propose(stories, _index(verdicts), [], {}, TH), K.MERGE)

    [related] = [e for e in merge.evidence if e.startswith("also related")]
    assert related == (
        "also related: US-1.2 ~ US-2.8 (o 0.48) · US-1.2 ~ US-2.7 (o 0.47) · "
        "US-1.2 ~ US-2.6 (o 0.46) · US-1.2 ~ US-2.5 (o 0.45) · US-1.2 ~ US-2.4 (o 0.44) · +3 more"
    )


def test_m8_write_outputs_header_uses_resolved_models_and_calibration(tmp_path: Path) -> None:
    """M8: proposals.md names the resolved model(s) and run_meta's calibration line."""
    stories = [_story("US-1.1"), _story("US-1.2")]
    run_meta = {
        "run_id": "20260926-141229",
        "snapshot_sha256": SHA,
        "model_requested": "typesafe/jev-1.13",
        "models_resolved": [MODEL, "typesafe/jev-1.13-20261001"],
        "thresholds": asdict(TH),
        "calibration": "rel.v1 FAIL (not trusted) · atomic.v2 PASS",
    }

    write_outputs(tmp_path, stories, [], {}, [], [], run_meta)

    lines = (tmp_path / "proposals.md").read_text("utf-8").splitlines()
    assert lines[0].endswith(f"· {MODEL}, typesafe/jev-1.13-20261001")
    assert lines[1] == "Calibration: rel.v1 FAIL (not trusted) · atomic.v2 PASS"


# ---------------------------------------------------------------- M2, M5 score_labels


def test_m2_score_labels_uses_the_averaged_verdict(tmp_path: Path) -> None:
    """M2: fwd m .7 and swap m .4 average to m .55, so Jev's class is merge, not overlap."""
    fwd = {"A_contains_B": 0.7, "partial_overlap": 0.2, "same_area_distinct": 0.1}
    swap_as_sent = {"B_contains_A": 0.4, "partial_overlap": 0.2, "same_area_distinct": 0.4}
    run_dir = _write_run(
        tmp_path / "run",
        ["US-1.1", "US-1.2"],
        [
            _pair_row("US-1.1", "US-1.2", "fwd", fwd),
            _pair_row("US-1.2", "US-1.1", "swap", swap_as_sent),
        ],
    )
    sheet = tmp_path / "labels.jsonl"
    _write_jsonl(sheet, [{"kind": "pair", "a": "US-1.1", "b": "US-1.2", "label": "merge"}])

    scores = score_labels(run_dir, sheet)

    assert scores["rel_agreement"] == pytest.approx(1.0)
    assert scores["rel_merge_recall"] == pytest.approx(1.0)


def _labels_run(tmp_path: Path) -> tuple[Path, Path]:
    """10 pairs: Jev merge x3, overlap x3, none x4. Labeller: merge at pairs 1, 2, 4.

    rel: agree 7/10; kappa (0.7 - 0.35) / 0.65; merge recall 2/3 -> FAIL.
    atomic: flagged >= 0.8 are .95 (split), .85 (keep), .82 (split) -> precision 2/3.
    AUC: positives .95 .82 .60 vs negatives .85 .30 .10 -> 3 + 2 + 2 = 7/9 -> PASS.
    proposals: agree 1 of 2.
    """
    ids = [f"US-1.{k}" for k in range(1, 7)]
    merge = {"A_contains_B": 0.7, "partial_overlap": 0.2, "same_area_distinct": 0.1}
    overlap = {"partial_overlap": 0.8, "same_area_distinct": 0.2}
    none = {"same_area_distinct": 0.7, "unrelated": 0.3}
    pairs = list(combinations(ids[:5], 2))
    jev = [merge] * 3 + [overlap] * 3 + [none] * 4
    labels = ["merge", "merge", "overlap", "merge", "overlap", "none"] + ["none"] * 4
    atomic = dict(zip(ids, (0.95, 0.85, 0.82, 0.60, 0.30, 0.10), strict=True))
    story_labels = ["split", "keep", "split", "split", "keep", "keep"]
    run_dir = _write_run(
        tmp_path / "run",
        ids,
        [_pair_row(a, b, "fwd", p) for (a, b), p in zip(pairs, jev, strict=True)],
        [_prop("M1", "merge", ["US-1.1", "US-1.2"], "US-1.1"), _prop("S1", "split", ["US-1.1"])],
        atomic,
    )
    sheet = tmp_path / "labels.jsonl"
    rows: list[dict[str, Any]] = [
        {"kind": "pair", "a": b, "b": a, "label": label}  # either order is accepted
        for (a, b), label in zip(pairs, labels, strict=True)
    ]
    rows += [
        {"kind": "story", "id": sid, "label": lab}
        for sid, lab in zip(ids, story_labels, strict=True)
    ]
    rows += [
        {"kind": "proposal", "id": "M1", "label": "agree"},
        {"kind": "proposal", "id": "S1", "label": "disagree"},
    ]
    _write_jsonl(sheet, rows)
    return run_dir, sheet


def test_m5_score_labels_scores_every_section_5_bar(tmp_path: Path) -> None:
    """M5 / §5: merge recall, atomic precision at 0.8 and AUC, and pass/fail per question."""
    run_dir, sheet = _labels_run(tmp_path)

    scores = score_labels(run_dir, sheet)

    assert scores["rel_agreement"] == pytest.approx(0.7)
    assert scores["rel_kappa"] == pytest.approx(0.35 / 0.65)
    assert scores["rel_merge_recall"] == pytest.approx(2 / 3)
    assert scores["rel_pass"] is False
    assert scores["atomic_precision"] == pytest.approx(2 / 3)
    assert scores["atomic_auc"] == pytest.approx(7 / 9)
    assert scores["atomic_pass"] is True
    assert scores["proposal_agreement"] == pytest.approx(0.5)
    assert json.loads(json.dumps(scores)) == scores
    assert calibration_line(scores) == "rel.v1 FAIL (not trusted) · atomic.v2 PASS"


@pytest.mark.parametrize(
    ("result", "line"),
    [
        ({"rel_pass": True, "atomic_pass": False}, "rel.v1 PASS · atomic.v2 FAIL (not trusted)"),
        ({"rel_pass": True}, "rel.v1 PASS · atomic.v2 not labelled"),
        ({}, "rel.v1 not labelled · atomic.v2 not labelled"),
    ],
)
def test_m5_calibration_line(result: dict[str, Any], line: str) -> None:
    """M5: the one-line summary written under the proposals.md header."""
    assert calibration_line(result) == line


@pytest.mark.parametrize(
    "bad_line",
    [
        "{not json",
        '{"kind": "pair", "a": "US-1.1", "b": "US-1.2", "label": "maybe"}',
        '{"kind": "story", "id": "US-1.1", "label": "merge"}',
        '{"kind": "pair", "a": "US-1.1", "b": "US-9.9", "label": "none"}',
        '["a", "list"]',
    ],
    ids=["not-json", "bad-pair-label", "bad-story-label", "unknown-story", "not-an-object"],
)
def test_m5_malformed_labels_are_input_errors(bad_line: str, tmp_path: Path) -> None:
    """A labels file that cannot be scored is an InputError (exit 4), not a traceback."""
    run_dir = _write_run(
        tmp_path / "run",
        ["US-1.1", "US-1.2"],
        [_pair_row("US-1.1", "US-1.2", "fwd", {"unrelated": 1.0})],
        atomic={"US-1.1": 0.5},
    )
    sheet = tmp_path / "labels.jsonl"
    sheet.write_text(bad_line + "\n", encoding="utf-8")

    with pytest.raises(InputError):
        score_labels(run_dir, sheet)


def test_eval_on_a_directory_that_is_not_a_run_is_an_input_error(tmp_path: Path) -> None:
    """score_labels, score_synthetic and make_label_sheet name the missing run file."""
    sheet = tmp_path / "labels.jsonl"
    sheet.write_text("", encoding="utf-8")
    truth = Truth(seed=1, n=2, duplicates=(), containments=(), bundled=())

    with pytest.raises(InputError, match="not a run"):
        score_labels(tmp_path, sheet)
    with pytest.raises(InputError, match="not a run"):
        score_synthetic(tmp_path, truth)
    with pytest.raises(InputError, match="not a run"):
        make_label_sheet(tmp_path, tmp_path / "out.jsonl")


# ---------------------------------------------------------------- M3 label sheet


def test_m3_label_sheet_is_shuffled_skips_leave_and_stays_in_the_run(tmp_path: Path) -> None:
    """M3: pair rows are not in band order, leave rows are dropped, only the run's stories appear.

    8 stories, 28 pairs with o rising by pair index, so an unshuffled sheet lists bands 0,0,1,1,..
    """
    ids = [f"US-1.{k}" for k in range(1, 9)]
    pairs = list(combinations(ids, 2))
    rows = [
        _pair_row(a, b, "fwd", {"partial_overlap": i / 28, "unrelated": 1 - i / 28})
        for i, (a, b) in enumerate(pairs)
    ]
    rows.append(_pair_row("US-1.1", "US-9.9", "fwd", {"partial_overlap": 0.9, "unrelated": 0.1}))
    proposals = [
        _prop("M1", "merge", ["US-1.1", "US-1.2"], "US-1.1"),
        _prop("M2", "merge", ["US-1.3", "US-9.9"], "US-1.3"),
        _prop("L1", "leave", ["US-1.8"]),
    ]
    run_dir = _write_run(tmp_path / "run", ids, rows, proposals)
    o_of = {frozenset(p): i / 28 for i, p in enumerate(pairs)}

    for seed in (7, 8):
        sheet = _read_jsonl(make_label_sheet(run_dir, tmp_path / f"s{seed}.jsonl", n=8, seed=seed))
        order = [o_of[frozenset((r["a"], r["b"]))] for r in sheet if r["kind"] == "pair"]
        assert len(order) == 8
        assert order != sorted(order)
        assert {r["id"] for r in sheet if r["kind"] == "proposal"} == {"M1"}
        assert "US-9.9" not in json.dumps(sheet)


def test_m3_label_sheet_rejects_a_negative_n(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run", ["US-1.1", "US-1.2"], [])

    with pytest.raises(UsageError):
        make_label_sheet(run_dir, tmp_path / "out.jsonl", n=-5)


# ---------------------------------------------------------------- M4, M7 score_synthetic


def test_m4_m7_synthetic_scores_jev_level_prefilter_and_container_splits(tmp_path: Path) -> None:
    """M4 / M7 (§6): Jev-level recall (o >= 0.5, averaged), pre-filter recall, container splits.

    Planted: dup 1.1~2.1 (o .6, kept), containment 3.1>1.2 (fwd o .4 + swap o .7 = .55, not kept),
    containment 2.2>3.2 (never asked). Unplanted 1.1~1.2 at o .8 (kept), 2.1~3.2 at o .1.
    jev_level: tp 2, fp 1, fn 1. prefilter: kept {1.1~2.1, 1.1~1.2} -> tp 1, fp 1, fn 2.
    Splits on 3.1 (a container) and 9.1 (a bundle): bundles tp 1, fp 0; container_splits tp 1/2.
    """
    ids = ["US-1.1", "US-1.2", "US-2.1", "US-2.2", "US-3.1", "US-3.2", "US-9.1"]
    rows = [
        _pair_row("US-1.1", "US-2.1", "fwd", {"duplicate": 0.3, "partial_overlap": 0.3}),
        _pair_row(
            "US-3.1", "US-1.2", "fwd", {"A_contains_B": 0.2, "partial_overlap": 0.2}, kept=False
        ),
        _pair_row(
            "US-1.2", "US-3.1", "swap", {"B_contains_A": 0.5, "partial_overlap": 0.2}, kept=False
        ),
        _pair_row("US-1.1", "US-1.2", "fwd", {"partial_overlap": 0.8, "unrelated": 0.2}),
        _pair_row("US-2.1", "US-3.2", "fwd", {"partial_overlap": 0.1}, kept=False),
    ]
    proposals = [_prop("S1", "split", ["US-3.1"]), _prop("S2", "split", ["US-9.1"])]
    run_dir = _write_run(tmp_path / "run", ids, rows, proposals)
    truth = Truth(
        seed=1,
        n=7,
        duplicates=(("US-1.1", "US-2.1"),),
        containments=(("US-3.1", "US-1.2"), ("US-2.2", "US-3.2")),
        bundled=("US-9.1",),
    )

    scores = score_synthetic(run_dir, truth)

    got = {k: (v.tp, v.fp, v.fn) for k, v in scores.items()}
    assert got["jev_level"] == (2, 1, 1)
    assert got["prefilter"] == (1, 1, 2)
    assert got["bundles"] == (1, 0, 0)
    assert got["container_splits"] == (1, 0, 1)
    assert scores["jev_level"].recall == pytest.approx(2 / 3)
    assert scores["prefilter"].recall == pytest.approx(1 / 3)
    assert scores["container_splits"].recall == pytest.approx(0.5)


# ---------------------------------------------------------------- M6, M7, L2 synth


def _parse(text: str, tmp_path: Path) -> tuple[Story, ...]:
    source = tmp_path / "stories.md"
    source.write_text(text, encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    parsed = parse_section(take_snapshot(source, run_dir).text)
    assert parsed.unparsed == ()
    return parsed.stories


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 20, 64])
def test_m6_generate_respects_n(n: int, tmp_path: Path) -> None:
    """M6: `n` live stories for small and above-default n; Truth.n says n; plants fit inside."""
    text, truth = generate(seed=42, n=n)

    ids = [s.id for s in _parse(text, tmp_path)]

    assert len(ids) == len(set(ids)) == n
    assert truth.n == n
    planted = {sid for pair in (*truth.duplicates, *truth.containments) for sid in pair}
    assert planted | set(truth.bundled) <= set(ids)
    if n >= 5:
        assert truth.duplicates and truth.containments and truth.bundled


@pytest.mark.parametrize("n", [0, -4, 500])
def test_m6_generate_rejects_n_out_of_range(n: int) -> None:
    """M6: n below 1 or above what the seeds can build is a usage error, not a wrong set."""
    with pytest.raises(UsageError):
        generate(seed=42, n=n)


def test_m7_container_title_is_the_addon_title(tmp_path: Path) -> None:
    """M7: a container reads as one capability (the addon title), not "X and Y" like a bundle."""
    text, truth = generate()
    by_id = {s.id: s for s in _parse(text, tmp_path)}
    raw = (files("tesselint") / "data" / "seeds.toml").read_text(encoding="utf-8")
    addon_title = {
        s["core"]: s["addon"]["title"] for s in tomllib.loads(raw)["seed"] if "addon" in s
    }

    for container, contained in truth.containments:
        assert by_id[container].title == addon_title[by_id[contained].core].rstrip(".")


def test_l2_bundles_record_their_parts_and_some_fuse_three_seeds(tmp_path: Path) -> None:
    """L2 / §6: truth keeps each bundle's seed ids; bundles fuse 2-3 seeds, at least one 3."""
    text, truth = generate()
    by_id = {s.id: s for s in _parse(text, tmp_path)}

    assert len(truth.bundle_parts) == len(truth.bundled)
    assert {len(parts) for parts in truth.bundle_parts} <= {2, 3}
    assert any(len(parts) == 3 for parts in truth.bundle_parts)
    for bundle_id, parts in zip(truth.bundled, truth.bundle_parts, strict=True):
        assert len(set(parts)) == len(parts), bundle_id
        assert " and " in by_id[bundle_id].title

    data = truth_to_dict(truth)
    assert data["bundled"][0] == {"id": truth.bundled[0], "parts": list(truth.bundle_parts[0])}
    assert data["containments"][0] == {
        "container": truth.containments[0][0],
        "contained": truth.containments[0][1],
    }


@pytest.mark.parametrize("seed", [42, 7])
def test_addon_only_fillers_have_their_own_title_and_a_standalone_core(seed: int) -> None:
    """An addon-only filler must not wear its container title (seed + addon) nor open with "I also":
    either makes it read as containing its base seed, an unplanted merge."""
    data = tomllib.loads((files("tesselint") / "data" / "seeds.toml").read_text("utf-8"))
    pairs = [(s["core"], s["addon"]) for s in data["seed"] if "addon" in s]
    container_titles = {add["title"].rstrip(".") for _, add in pairs}
    story = re.compile(
        r"^- \*\*US-\d+\.\d+(?: \[[^\]]*\])? (?P<title>.+?)\.\*\*(?: (?P<core>.*))?$"
    )

    text, _ = generate(seed=seed, n=60)
    fillers: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = story.match(line)
        if m is None or not m["core"]:
            continue
        for base, add in pairs:  # the addon's own sentence without its base seed = a filler
            if add["core"].removeprefix("I also ") in m["core"] and base not in m["core"]:
                fillers.append((m["title"], m["core"]))
                break

    assert fillers, "n=60 runs out of seeds and should need addon-only fillers"
    for title, core in fillers:
        assert title not in container_titles, title
        assert not core.startswith("I also"), core


def test_truth_json_round_trips_and_bad_files_are_input_errors(tmp_path: Path) -> None:
    """truth.json written by truth_to_dict loads back; the old id-list shape loads too."""
    _, truth = generate(seed=3, n=30)
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(truth_to_dict(truth)), encoding="utf-8")

    assert load_truth(path) == truth

    old = {**truth_to_dict(truth), "bundled": list(truth.bundled)}
    path.write_text(json.dumps(old), encoding="utf-8")
    assert load_truth(path).bundled == truth.bundled

    for bad in ("{", json.dumps({"seed": 1}), json.dumps({**old, "duplicates": [["US-1.1"]]})):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(InputError):
            load_truth(path)
    with pytest.raises(InputError):
        load_truth(tmp_path / "missing.json")


# ---------------------------------------------------------------- L1, L3


def test_l1_split_word_gate_counts_the_full_text() -> None:
    """L1: a 5-word core with sub-bullets is asked (full text >= 8 words), so it can be split."""
    story = _story(
        "US-1.1", title="Plan", core="I plan my beds.", subs=("Beds.", "Rent.", "Tools.")
    )
    assert len(render(story, "core").split()) < TH.min_words_atomic <= len(render(story).split())

    splits = _of(propose([story], {}, [], {"US-1.1": 0.9}, TH), K.SPLIT)

    assert [p.stories for p in splits] == [("US-1.1",)]


def test_l3_atomic_skipped_only_when_the_story_was_not_asked(tmp_path: Path) -> None:
    """L3: a failed atomic call leaves atomic null but not skipped; an unasked story is skipped."""
    stories = [_story("US-1.1"), _story("US-1.2")]
    text = render(stories[0])
    job = Job(
        key="atomic:US-1.1",
        a="US-1.1",
        b=None,
        order="fwd",
        state=text,
        question=ATOMIC_V2,
        est_tokens=400,
        text_hash_a=text_hash(text),
        text_hash_b=None,
    )
    failed = JobResult(job=job, answer=None, cached=False, attempts=6, error="HTTP 503")

    write_outputs(tmp_path, stories, [failed], {}, [], [], {"thresholds": asdict(TH)})

    rows = {
        r["id"]: (r["atomic"], r["atomic_skipped"]) for r in _read_jsonl(tmp_path / "stories.jsonl")
    }
    assert rows == {"US-1.1": (None, False), "US-1.2": (None, True)}


# ---------------------------------------------------------------- folded merges, hubs, summary

HUB_STORIES = [
    _story(sid, "E" + sid.removeprefix("US-").split(".")[0])
    for sid in ("US-1.1", "US-1.2", "US-1.3", "US-2.1", "US-2.2", "US-2.3", "US-3.1")
]


def _hub_run() -> list[Proposal]:
    """US-2.1 is contained by US-1.1 (m .90), US-1.2 (.80) and US-1.3 (.70); US-1.1 also contains
    US-2.2 (.85) and US-2.3 (.60, borderline) and is bundled .85 (borderline split S1). US-3.1 is
    left alone.

    Merges by m: M1 2.1→1.1, M2 2.2→1.1, M3 2.1→1.2, M4 2.1→1.3, M5 2.3→1.1. M1 is US-2.1's
    strongest merge, so M3 and M4 fold under it; US-1.1 keeps M1, M2 and M5, which makes it a hub.
    """
    contains = [
        ("US-1.1", "US-2.1", 0.90),
        ("US-1.1", "US-2.2", 0.85),
        ("US-1.2", "US-2.1", 0.80),
        ("US-1.3", "US-2.1", 0.70),
        ("US-1.1", "US-2.3", 0.60),
    ]
    verdicts = _index(
        _pv(keep, drop, {"A_contains_B": m, "unrelated": round(1 - m, 2)})
        for keep, drop, m in contains
    )
    return propose(HUB_STORIES, verdicts, [], {"US-1.1": 0.85}, TH)


def test_merge_section_folds_weaker_containers_into_the_strongest_merge() -> None:
    """One entry per merged-away story; its other containers fold into one bullet, ids kept."""
    proposals = _hub_run()
    merges = _of(proposals, K.MERGE)
    assert [p.id for p in merges] == ["M1", "M2", "M3", "M4", "M5"]  # proposals.json unchanged

    text = render_proposals(proposals, HUB_STORIES, {})

    lines = text.splitlines()
    headings = [line for line in lines if line.startswith("### M")]
    assert headings == [
        "### M1 · merge US-2.1 → US-1.1 (containment) · m 0.90",
        "### M2 · merge US-2.2 → US-1.1 (containment) · m 0.85",
        "### M5 · merge US-2.3 → US-1.1 (containment) · m 0.60 · borderline",
    ]
    m1 = lines[lines.index(headings[0]) : lines.index(headings[1])]
    assert "- also contained by: US-1.2 (m 0.80, M3), US-1.3 (m 0.70, M4)" in m1
    assert "- ⚠ conflicts with S1" in m1  # the split conflict stays flagged
    assert all(_mentions(text, p.id) for p in merges)  # folded ids are not lost


def test_a_story_kept_by_three_merges_is_listed_under_hubs_before_merge() -> None:
    """US-1.1 keeps M1, M2 and M5: a hub, with its contained stories by m and its split linked."""
    lines = render_proposals(_hub_run(), HUB_STORIES, {}).splitlines()

    sections = [line for line in lines if line.startswith("## ")]
    assert sections[:2] == ["## Hubs", "## Merge"]
    hubs = lines[lines.index("## Hubs") : lines.index("## Merge")]
    assert [line for line in hubs if line.startswith("- ")] == [
        '- US-1.1 "Inspection record 1.1" contains 3: US-2.1 (m 0.90), US-2.2 (m 0.85), '
        "US-2.3 (m 0.60) · split [S1](#S1) (bundled 0.85)"
    ]
    no_hub = render_proposals(_md_proposals(), MD_STORIES, {}).splitlines()
    assert "## Hubs" not in no_hub  # no container keeps three merges


def test_summary_counts_kinds_and_links_the_strongest_merges_and_splits() -> None:
    """7 stories: 3 to merge away (2 folded; M5 at m .60 is borderline), 1 split, 1 left alone."""
    lines = render_proposals(_hub_run(), HUB_STORIES, {}).splitlines()

    assert lines[3] == (
        "Summary: 7 stories · 3 stories to merge away (2 alternative containers folded, "
        "1 borderline) · "
        "0 overlaps · 1 split (1 borderline) · 1 leave alone"
    )
    top = lines.index("Top merges (3 of 3):")
    assert lines[top + 1 : top + 4] == [
        '- [M1](#M1) US-2.1 "Inspection record 2.1" → US-1.1 "Inspection record 1.1" · m 0.90',
        '- [M2](#M2) US-2.2 "Inspection record 2.2" → US-1.1 "Inspection record 1.1" · m 0.85',
        '- [M5](#M5) US-2.3 "Inspection record 2.3" → US-1.1 "Inspection record 1.1" · m 0.60'
        " · borderline",
    ]
    assert '- [S1](#S1) US-1.1 "Inspection record 1.1" · bundled 0.85 · borderline' in lines
    assert top < min(i for i, line in enumerate(lines) if line.startswith("How to read this"))
    assert {'<a id="M1"></a>', '<a id="S1"></a>'} <= set(lines)  # the links have targets


def test_summary_lists_at_most_ten_merges() -> None:
    """12 merges of 12 different stories: the summary shows the strongest 10 of 12."""
    merges = [
        Proposal(f"M{k}", K.MERGE, "US-1.1", ("US-1.1", f"US-2.{k}"), 0.9 - k / 100, False, ())
        for k in range(1, 13)
    ]

    lines = render_proposals(merges, MD_STORIES, {}).splitlines()

    top = lines.index("Top merges (10 of 12):")
    shown = [line for line in lines[top + 1 :] if line.startswith("- [M")]
    assert [line.split()[1] for line in shown] == [f"[M{k}](#M{k})" for k in range(1, 11)]
