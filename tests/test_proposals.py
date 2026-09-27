"""proposals: merge, overlap, split and leave from verdicts, clusters and atomicity (DESIGN §2.5).

Planted verdicts are in document order (a before b in `stories`). Proposal ids are M<n> for
merges, O<n> for overlaps and S<n> for splits (DESIGN §9); leave proposals are only checked by
the stories they cover, so one per story or one per epic both pass.
"""

import re
from collections.abc import Sequence

import pytest

from tesselint.cluster import Cluster
from tesselint.models import ProposalKind, Relation, Story, Thresholds
from tesselint.proposals import Proposal, propose, render_proposals
from tesselint.verdicts import PairVerdict

R = Relation
K = ProposalKind
TH = Thresholds()
LONG_CORE = (
    "I record every plot inspection on my site with photos and notes, "
    "so that the committee can follow up on problems."
)


def _story(sid: str, epic: str, *, short: bool = False) -> Story:
    line = sum(ord(ch) for ch in sid)
    if short:  # "Export. I export it." is 4 words under any rendering.
        return Story(
            id=sid, epic=epic, title="Export", tag="MVP", core="I export it.", subs=(), line=line
        )
    return Story(
        id=sid,
        epic=epic,
        title="Inspection record",
        tag="MVP",
        core=LONG_CORE,
        subs=("Each record names the plot.",),
        line=line,
    )


def _pv(a: str, b: str, **probs: float) -> PairVerdict:
    """A verdict in document order; keys are Relation values, e.g. A_contains_B=0.7."""
    p = {Relation(k): v for k, v in probs.items()}
    merge = p.get(R.DUPLICATE, 0.0) + p.get(R.A_CONTAINS_B, 0.0) + p.get(R.B_CONTAINS_A, 0.0)
    overlap = merge + p.get(R.PARTIAL, 0.0)
    return PairVerdict(
        a=a,
        b=b,
        probs=p,
        fwd=p,
        swap=None,
        overlap=overlap,
        merge=merge,
        relation=max(p, key=lambda r: p[r]),
    )


def _index(verdicts: Sequence[PairVerdict]) -> dict[tuple[str, str], PairVerdict]:
    return {(v.a, v.b): v for v in verdicts}


def _of(proposals: Sequence[Proposal], kind: ProposalKind) -> list[Proposal]:
    return [p for p in proposals if p.kind == kind]


def _mentions(text: str, sid: str) -> bool:
    """True when `sid` appears as a whole ID (US-1.1 does not match inside US-1.11)."""
    return re.search(rf"(?<![\w-]){re.escape(sid)}(?!\d)", text) is not None


PAIR = [_story("US-1.9", "E1"), _story("US-1.10", "E1")]  # document order differs from ID order


@pytest.mark.parametrize(
    ("probs", "target", "stories", "m", "borderline"),
    [
        (
            {"A_contains_B": 0.7, "partial_overlap": 0.2, "same_area_distinct": 0.1},
            "US-1.9",
            ("US-1.9", "US-1.10"),
            0.7,
            False,
        ),
        (
            {"B_contains_A": 0.7, "partial_overlap": 0.2, "same_area_distinct": 0.1},
            "US-1.10",
            ("US-1.10", "US-1.9"),
            0.7,
            False,
        ),
        (
            {"duplicate": 0.7, "partial_overlap": 0.2, "same_area_distinct": 0.1},
            "US-1.9",
            ("US-1.9", "US-1.10"),
            0.7,
            False,
        ),
        (
            {
                "duplicate": 0.35,
                "B_contains_A": 0.3,
                "A_contains_B": 0.05,
                "partial_overlap": 0.2,
                "same_area_distinct": 0.1,
            },
            "US-1.9",
            ("US-1.9", "US-1.10"),
            0.7,
            False,
        ),
        (
            {
                "duplicate": 0.2,
                "B_contains_A": 0.4,
                "A_contains_B": 0.05,
                "partial_overlap": 0.25,
                "same_area_distinct": 0.1,
            },
            "US-1.10",
            ("US-1.10", "US-1.9"),
            0.65,
            False,
        ),
        (
            {"A_contains_B": 0.5, "partial_overlap": 0.4, "same_area_distinct": 0.1},
            "US-1.9",
            ("US-1.9", "US-1.10"),
            0.5,
            True,
        ),
    ],
    ids=[
        "a-contains-b",
        "b-contains-a",
        "duplicate-later-into-earlier",
        "duplicate-is-largest-merge-prob",
        "containment-is-largest-merge-prob",
        "m-at-threshold-is-borderline",
    ],
)
def test_merge_keeps_container_or_earlier_duplicate(
    probs: dict[str, float],
    target: str,
    stories: tuple[str, ...],
    m: float,
    borderline: bool,
) -> None:
    """§2.5 merge: m >= 0.5; the largest merge prob picks the direction; §9 lists the kept first."""
    verdict = _pv("US-1.9", "US-1.10", **probs)
    cluster = Cluster(id="C1", members=("US-1.9", "US-1.10"), mean_overlap=verdict.overlap)

    proposals = propose(PAIR, _index([verdict]), [cluster], {"US-1.9": 0.1, "US-1.10": 0.1}, TH)

    merges = _of(proposals, K.MERGE)
    assert len(merges) == 1
    merge = merges[0]
    assert merge.id == "M1"
    assert merge.target == target
    assert merge.stories == stories
    assert merge.decisive_value == pytest.approx(m)
    assert merge.borderline is borderline
    assert _of(proposals, K.OVERLAP) == []  # the single merge covers the 2-story cluster


def test_merge_below_threshold_leaves_an_overlap() -> None:
    """§2.5: m < 0.5 is no merge; the cluster no merge covers becomes an overlap on its mean o."""
    verdict = _pv(
        "US-1.9", "US-1.10", A_contains_B=0.45, partial_overlap=0.45, same_area_distinct=0.1
    )
    cluster = Cluster(id="C1", members=("US-1.9", "US-1.10"), mean_overlap=0.9)

    proposals = propose(PAIR, _index([verdict]), [cluster], {"US-1.9": 0.1, "US-1.10": 0.1}, TH)

    assert _of(proposals, K.MERGE) == []
    overlaps = _of(proposals, K.OVERLAP)
    assert len(overlaps) == 1
    assert overlaps[0].id == "O1"
    assert overlaps[0].target == "C1"
    assert overlaps[0].stories == ("US-1.9", "US-1.10")
    assert overlaps[0].decisive_value == pytest.approx(0.9)


SPLIT_STORIES = [
    _story("US-1.1", "E1"),
    _story("US-1.2", "E1"),
    _story("US-2.1", "E2"),
    _story("US-2.2", "E2"),
    _story("US-9.1", "E9"),
]
SPLIT_CLUSTERS = [
    Cluster(id="C1", members=("US-1.1", "US-1.2"), mean_overlap=0.8),
    Cluster(id="C2", members=("US-2.1", "US-2.2"), mean_overlap=0.8),
]


@pytest.mark.parametrize(
    ("atomic", "neighbours", "edge_o", "split", "borderline"),
    [
        (0.95, (), 0.0, True, False),
        (0.85, (), 0.0, True, True),
        (0.80, (), 0.0, True, True),
        (0.79, (), 0.0, False, False),
        (0.75, ("US-1.1", "US-2.1"), 0.55, True, True),
        (0.70, ("US-1.1", "US-2.1"), 0.5, True, True),
        (0.75, ("US-1.1", "US-1.2"), 0.55, False, False),
        (0.75, ("US-1.1", "US-2.1"), 0.45, False, False),
        (0.69, ("US-1.1", "US-2.1"), 0.55, False, False),
        (None, (), 0.0, False, False),
    ],
    ids=[
        "clear",
        "borderline",
        "at-threshold",
        "below",
        "corroborated",
        "corroborated-at-thresholds",
        "edges-into-one-cluster",
        "edges-too-weak",
        "below-corroborated",
        "not-asked",
    ],
)
def test_split_on_atomicity_or_corroborated_by_two_clusters(
    atomic: float | None,
    neighbours: tuple[str, ...],
    edge_o: float,
    split: bool,
    borderline: bool,
) -> None:
    """§2.5 split: atomic >= 0.80, or >= 0.70 with o >= 0.5 edges into >= 2 other clusters."""
    verdicts = [
        _pv("US-1.1", "US-1.2", partial_overlap=0.8, same_area_distinct=0.2),
        _pv("US-2.1", "US-2.2", partial_overlap=0.8, same_area_distinct=0.2),
    ]
    for other in neighbours:
        verdicts.append(
            _pv(other, "US-9.1", partial_overlap=edge_o, same_area_distinct=1.0 - edge_o)
        )
    scores: dict[str, float | None] = {s.id: 0.1 for s in SPLIT_STORIES}
    scores["US-9.1"] = atomic

    proposals = propose(SPLIT_STORIES, _index(verdicts), SPLIT_CLUSTERS, scores, TH)

    splits = _of(proposals, K.SPLIT)
    if not split:
        assert splits == []
        return
    assert len(splits) == 1
    assert splits[0].id == "S1"
    assert splits[0].target is None
    assert splits[0].stories == ("US-9.1",)
    assert splits[0].decisive_value == pytest.approx(atomic)
    assert splits[0].borderline is borderline


@pytest.mark.parametrize("atomic", [None, 0.95])
def test_story_under_eight_words_is_never_split(atomic: float | None) -> None:
    """§2.4: stories under 8 words get no call and no split, whatever score they carry."""
    stories = [_story("US-1.1", "E1"), _story("US-1.2", "E1", short=True)]

    proposals = propose(stories, {}, [], {"US-1.1": 0.1, "US-1.2": atomic}, TH)

    assert _of(proposals, K.SPLIT) == []
    leave = {sid for p in _of(proposals, K.LEAVE) for sid in p.stories}
    assert leave == {"US-1.1", "US-1.2"}


def test_merge_evidence_names_also_related_stories() -> None:
    """§2.5 evidence: "also related" lists o >= 0.4 neighbours (US-1.1 x US-20.1 at 0.45)."""
    stories = [
        _story("US-1.1", "E2"),
        _story("US-20.1", "E20"),
        _story("US-20.2", "E20"),
        _story("US-9.1", "E9"),
    ]
    verdicts = [
        _pv("US-1.1", "US-20.2", A_contains_B=0.53, partial_overlap=0.4, same_area_distinct=0.07),
        _pv("US-1.1", "US-20.1", partial_overlap=0.45, same_area_distinct=0.55),
        _pv("US-1.1", "US-9.1", partial_overlap=0.35, same_area_distinct=0.65),
        _pv("US-20.1", "US-20.2", same_area_distinct=1.0),
    ]
    clusters = [Cluster(id="C1", members=("US-1.1", "US-20.2"), mean_overlap=0.93)]
    atomic: dict[str, float | None] = {s.id: 0.1 for s in stories}

    proposals = propose(stories, _index(verdicts), clusters, atomic, TH)

    merges = _of(proposals, K.MERGE)
    assert [(p.target, p.stories) for p in merges] == [("US-1.1", ("US-1.1", "US-20.2"))]
    assert merges[0].decisive_value == pytest.approx(0.53)
    assert merges[0].borderline is True
    related = [e for e in merges[0].evidence if "also related" in e]
    assert any(_mentions(e, "US-20.1") for e in related)
    assert not any(_mentions(e, "US-9.1") for e in merges[0].evidence)
    leave = {sid for p in _of(proposals, K.LEAVE) for sid in p.stories}
    assert leave == {"US-20.1", "US-9.1"}


def test_every_story_gets_a_proposal_and_the_rest_is_leave() -> None:
    """§2.5: merges, an overlap for the cluster no merge covers, a split; everything else leave."""
    stories = [
        _story("US-1.9", "E1"),
        _story("US-1.10", "E1"),
        _story("US-2.1", "E2"),
        _story("US-2.2", "E2"),
        _story("US-2.3", "E2"),
        _story("US-3.1", "E3"),
        _story("US-3.2", "E3"),
        _story("US-3.3", "E3", short=True),
    ]
    verdicts = [
        _pv("US-1.9", "US-1.10", duplicate=0.7, partial_overlap=0.2, same_area_distinct=0.1),
        _pv("US-2.1", "US-2.2", B_contains_A=0.6, partial_overlap=0.3, same_area_distinct=0.1),
        _pv("US-2.1", "US-2.3", partial_overlap=0.7, same_area_distinct=0.3),
        _pv("US-2.2", "US-2.3", partial_overlap=0.6, same_area_distinct=0.4),
        _pv("US-3.1", "US-3.2", same_area_distinct=0.9, unrelated=0.1),
    ]
    clusters = [
        Cluster(id="C1", members=("US-1.9", "US-1.10"), mean_overlap=0.9),
        Cluster(
            id="C2", members=("US-2.1", "US-2.2", "US-2.3"), mean_overlap=(0.9 + 0.7 + 0.6) / 3
        ),
    ]
    atomic: dict[str, float | None] = {s.id: 0.1 for s in stories}
    atomic.update({"US-3.1": 0.9, "US-3.2": 0.3, "US-3.3": 0.95})

    proposals = propose(stories, _index(verdicts), clusters, atomic, TH)

    merges = _of(proposals, K.MERGE)
    assert sorted(p.id for p in merges) == ["M1", "M2"]
    assert {(p.target, p.stories) for p in merges} == {
        ("US-1.9", ("US-1.9", "US-1.10")),
        ("US-2.2", ("US-2.2", "US-2.1")),
    }
    overlaps = _of(proposals, K.OVERLAP)
    assert [(p.id, p.target, p.stories) for p in overlaps] == [
        ("O1", "C2", ("US-2.1", "US-2.2", "US-2.3"))
    ]
    assert overlaps[0].decisive_value == pytest.approx(0.7333333)
    splits = _of(proposals, K.SPLIT)
    assert [(p.id, p.target, p.stories) for p in splits] == [("S1", None, ("US-3.1",))]
    acted = {sid for p in proposals if p.kind != K.LEAVE for sid in p.stories}
    leave = [sid for p in _of(proposals, K.LEAVE) for sid in p.stories]
    assert sorted(leave) == ["US-3.2", "US-3.3"]
    assert acted | set(leave) == {s.id for s in stories}


def test_render_proposals_lists_ids_kind_headings_and_header() -> None:
    """§9 proposals.md: header line, Merge/Split/Leave alone (N) headings, every proposal and ID."""
    stories = [
        _story("US-1.2", "E1"),
        _story("US-1.3", "E1"),
        _story("US-1.6", "E1"),
        _story("US-1.1", "E2"),
        _story("US-2.4", "E2"),
        _story("US-20.1", "E20"),
        _story("US-20.2", "E20"),
        _story("US-3.3", "E3"),
    ]
    proposals = [
        Proposal(
            id="M1",
            kind=K.MERGE,
            target="US-1.1",
            stories=("US-1.1", "US-20.2"),
            decisive_value=0.53,
            borderline=True,
            evidence=("fwd partial 0.53 / A>B 0.37", "also related: US-20.1 (o 0.45)"),
        ),
        Proposal(
            id="O1",
            kind=K.OVERLAP,
            target="C2",
            stories=("US-1.2", "US-1.6", "US-2.4"),
            decisive_value=0.78,
            borderline=False,
            evidence=(),
        ),
        Proposal(
            id="S1",
            kind=K.SPLIT,
            target=None,
            stories=("US-3.3",),
            decisive_value=0.85,
            borderline=True,
            evidence=("tag [MVP; the brief Lite]",),
        ),
        Proposal(
            id="L1",
            kind=K.LEAVE,
            target=None,
            stories=("US-1.3",),
            decisive_value=0.0,
            borderline=False,
            evidence=(),
        ),
        Proposal(
            id="L2",
            kind=K.LEAVE,
            target=None,
            stories=("US-20.1",),
            decisive_value=0.0,
            borderline=False,
            evidence=(),
        ),
    ]
    header = {
        "run_id": "20260926-141200",
        "snapshot_sha256": "b2dd2ef8",
        "model": "typesafe/jev-1.13-20260917",
    }

    text = render_proposals(proposals, stories, header)

    lines = text.splitlines()
    assert lines[0].startswith("# tesselint proposals")
    for value in header.values():
        assert value in lines[0]
    for heading in ("## Merge", "## Split", "## Leave alone (2)"):
        assert heading in lines
    for pid in ("M1", "O1", "S1"):
        assert any(line.startswith(f"### {pid} ") for line in lines)
    for story in stories:
        assert _mentions(text, story.id), story.id
    assert "C2" in text
