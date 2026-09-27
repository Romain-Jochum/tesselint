"""Local embedding shortlist, computed on every run and applied only with --prefilter (§2.2)."""

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

COS_DECIMALS = 4  # float noise in the last bits differs between platforms; runs never show it


def rounded_cos(value: float) -> float:
    """A cosine as runs write and show it: 4 decimals. Keep decisions use the raw similarity."""
    return round(float(value), COS_DECIMALS)


def similarity(
    texts: Sequence[str], model_name: str = "minishlab/potion-retrieval-32M"
) -> NDArray[np.float64]:
    """Return the cosine similarity matrix of model2vec embeddings of `texts` (DESIGN §2.2)."""
    if not texts:
        return np.empty((0, 0), dtype=np.float64)

    from model2vec import StaticModel

    model = StaticModel.from_pretrained(model_name)
    embs = model.encode(list(texts))
    norm = np.linalg.norm(embs, axis=1, keepdims=True)
    norm = np.where(norm == 0.0, 1.0, norm)
    embs_norm = np.asarray(embs / norm, dtype=np.float64)
    return embs_norm @ embs_norm.T  # float64: a float32 BLAS product differs across platforms


def keep_pairs(sim: NDArray[np.float64], k: int = 10, top_pct: float = 2.0) -> set[tuple[int, int]]:
    """Return index pairs (i < j) in each row's top k or the top `top_pct` % overall (§2.2)."""
    n = sim.shape[0]
    kept: set[tuple[int, int]] = set()

    # 1. Row-level top k nearest neighbours (excluding diagonal i == j)
    if k > 0:
        for i in range(n):
            others = [j for j in range(n) if j != i]
            sorted_others = sorted(others, key=lambda j: float(sim[i, j]), reverse=True)
            for j in sorted_others[:k]:
                kept.add((min(i, j), max(i, j)))

    # 2. Global top `top_pct` % of all pairs (i < j)
    if top_pct > 0.0:
        all_pairs: list[tuple[tuple[int, int], float]] = []
        for i in range(n):
            for j in range(i + 1, n):
                all_pairs.append(((i, j), float(sim[i, j])))
        all_pairs.sort(key=lambda item: item[1], reverse=True)
        num_global = round(len(all_pairs) * (top_pct / 100.0))
        for pair, _ in all_pairs[:num_global]:
            kept.add(pair)

    return kept
