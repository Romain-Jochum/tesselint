"""cluster: average linkage (UPGMA) on 1 - o, cut at o >= threshold (DESIGN §2.5).

Planted matrices hold o with NaN on the diagonal and for pairs never asked, as overlap_matrix
returns them. Expected output: singletons dropped; members in `ids` order; clusters numbered
C1, C2, ... by mean_overlap descending, ties by the position of their first member (DESIGN §9's
example has C1 at 0.95 and C2 at 0.78 although C2's stories come first in the document).
mean_overlap is the mean o over all member pairs, a pair never asked counting as 0.
"""

from collections.abc import Mapping, Sequence

import numpy as np
import pytest
from numpy.typing import NDArray

from tesselint.cluster import Cluster, average_linkage

NAN = float("nan")


def _matrix(ids: Sequence[str], edges: Mapping[tuple[str, str], float]) -> NDArray[np.float64]:
    index = {sid: i for i, sid in enumerate(ids)}
    matrix = np.full((len(ids), len(ids)), NAN, dtype=np.float64)
    for (a, b), o in edges.items():
        matrix[index[a], index[b]] = o
        matrix[index[b], index[a]] = o
    return matrix


def _shape(clusters: Sequence[Cluster]) -> list[tuple[str, tuple[str, ...]]]:
    return [(c.id, tuple(c.members)) for c in clusters]


def test_chain_is_cut_where_single_linkage_would_merge_it() -> None:
    """§2.5: A-B-C-D chained by o >= 0.75 splits into {A,B} and {C,D}; components would join all."""
    ids = ["US-1.1", "US-1.2", "US-1.3", "US-1.4", "US-1.5"]
    edges = {
        ("US-1.1", "US-1.2"): 0.85,
        ("US-1.2", "US-1.3"): 0.80,
        ("US-1.3", "US-1.4"): 0.75,
        ("US-1.1", "US-1.3"): 0.10,
        ("US-1.2", "US-1.4"): 0.10,
        ("US-1.1", "US-1.4"): 0.0,
    }
    # UPGMA on d = 1 - o: {1.1,1.2} at 0.15, {1.3,1.4} at 0.25, then the two at
    # (0.9 + 1.0 + 0.2 + 0.9) / 4 = 0.75 > 0.5, so the cut keeps them apart. US-1.5 is a singleton.

    clusters = average_linkage(ids, _matrix(ids, edges), threshold=0.5)

    assert _shape(clusters) == [("C1", ("US-1.1", "US-1.2")), ("C2", ("US-1.3", "US-1.4"))]
    assert [c.mean_overlap for c in clusters] == pytest.approx([0.85, 0.75])


def test_two_cliques_become_two_ordered_clusters() -> None:
    """§2.5/§9: interleaved cliques split cleanly; ids by mean o, members in ids order; stable."""
    ids = ["US-1.1", "US-1.2", "US-1.3", "US-1.4", "US-1.5", "US-1.6"]
    edges = {
        ("US-1.1", "US-1.3"): 0.9,
        ("US-1.1", "US-1.5"): 0.8,
        ("US-1.3", "US-1.5"): 0.7,
        ("US-1.2", "US-1.4"): 0.95,
        ("US-1.1", "US-1.2"): 0.2,
        ("US-1.3", "US-1.4"): 0.15,
    }
    matrix = _matrix(ids, edges)

    clusters = average_linkage(ids, matrix)

    assert _shape(clusters) == [
        ("C1", ("US-1.2", "US-1.4")),
        ("C2", ("US-1.1", "US-1.3", "US-1.5")),
    ]
    assert [c.mean_overlap for c in clusters] == pytest.approx([0.95, 0.8])
    assert average_linkage(ids, matrix.copy()) == clusters


@pytest.mark.parametrize(
    ("ids", "edges", "expected"),
    [
        (
            ["A", "B", "C"],
            {("A", "B"): 0.95, ("B", "C"): 0.9, ("A", "C"): NAN},
            [("C1", ("A", "B"), 0.95)],
        ),
        (
            ["A", "B", "C"],
            {("A", "B"): 0.95, ("B", "C"): 0.9, ("A", "C"): 0.0},
            [("C1", ("A", "B"), 0.95)],
        ),
        (
            ["A", "B", "C", "D"],
            {
                ("A", "B"): 0.9,
                ("A", "C"): 0.9,
                ("A", "D"): 0.9,
                ("B", "C"): 0.9,
                ("B", "D"): 0.9,
                ("C", "D"): NAN,
            },
            [("C1", ("A", "B", "C", "D"), 0.75)],
        ),
    ],
    ids=["nan-blocks-join", "zero-blocks-join", "nan-is-zero-in-mean"],
)
def test_pairs_never_asked_count_as_zero(
    ids: list[str],
    edges: dict[tuple[str, str], float],
    expected: list[tuple[str, tuple[str, ...], float]],
) -> None:
    """§2.5: NaN counts as o = 0 in linkage and mean_overlap ({A,B} to C: (1 + 0.1) / 2 > 0.5)."""
    clusters = average_linkage(ids, _matrix(ids, edges))

    assert _shape(clusters) == [(cid, members) for cid, members, _ in expected]
    assert [c.mean_overlap for c in clusters] == pytest.approx([mean for _, _, mean in expected])


@pytest.mark.parametrize(
    ("o", "threshold", "together"),
    [(0.5, 0.5, True), (0.49, 0.5, False), (0.75, 0.7, True), (0.6, 0.7, False)],
)
def test_cut_keeps_pairs_at_or_above_threshold(o: float, threshold: float, together: bool) -> None:
    """§2.5: the cut is o >= threshold, inclusive."""
    ids = ["US-1.1", "US-1.2", "US-1.3"]

    clusters = average_linkage(ids, _matrix(ids, {("US-1.1", "US-1.2"): o}), threshold=threshold)

    expected = [("C1", ("US-1.1", "US-1.2"))] if together else []
    assert _shape(clusters) == expected


@pytest.mark.parametrize("ids", [[], ["US-1.1"], ["US-1.1", "US-1.2", "US-1.3"]])
def test_no_clusters_without_an_asked_pair(ids: list[str]) -> None:
    """§2.5: zero or one story, or nothing asked, gives no clusters (singletons are dropped)."""
    assert average_linkage(ids, _matrix(ids, {})) == []
