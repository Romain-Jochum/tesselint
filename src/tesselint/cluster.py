"""Average-linkage clusters over the overlap matrix (DESIGN §2.5, §9 clusters.json)."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

# Averaged distances pick up float noise (0.5000000000000001); the cut at o >= threshold must not.
_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class Cluster:
    """A group of overlapping stories and the mean overlap among them."""

    id: str
    members: tuple[str, ...]
    mean_overlap: float


def average_linkage(
    ids: Sequence[str], overlap: NDArray[np.float64], threshold: float = 0.5
) -> list[Cluster]:
    """Cluster ids by average linkage on overlap, cut at `threshold`; drop singletons (§2.5)."""
    n = len(ids)
    if n <= 1:
        return []

    off_diag = overlap[~np.eye(n, dtype=bool)]
    if np.all(np.isnan(off_diag)):
        return []

    o_clean = np.where(np.isnan(overlap), 0.0, overlap)
    dist = 1.0 - o_clean
    np.fill_diagonal(dist, 0.0)

    condensed = squareform(dist, checks=False)
    z = linkage(condensed, method="average")
    labels = fcluster(z, t=1.0 - threshold + _EPS, criterion="distance")

    groups: dict[int, list[int]] = {}
    for idx, lbl in enumerate(labels):
        groups.setdefault(lbl, []).append(idx)

    raw_clusters: list[tuple[float, int, tuple[str, ...]]] = []
    for grp in groups.values():
        if len(grp) < 2:
            continue
        member_ids = tuple(ids[i] for i in sorted(grp))
        total_o = 0.0
        k = len(grp)
        for i in range(k):
            for j in range(i + 1, k):
                val = overlap[grp[i], grp[j]]
                if not np.isnan(val):
                    total_o += float(val)
        mean_o = total_o / (k * (k - 1) / 2)
        raw_clusters.append((mean_o, min(grp), member_ids))

    raw_clusters.sort(key=lambda x: (-round(x[0], 9), x[1]))
    return [
        Cluster(id=f"C{i + 1}", members=members, mean_overlap=mean_o)
        for i, (mean_o, _, members) in enumerate(raw_clusters)
    ]
