"""prefilter: model2vec cosine shortlist, per-story top-k union global top pct (DESIGN §2.2).

PREFILTER.md recommendation: keep a pair if either story has the other among its k nearest
neighbours, or if its score is in the global top `top_pct` % of pairs. Pairs are (i, j), i < j.
"""

from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from tesselint.parse import parse_section
from tesselint.prefilter import keep_pairs, rounded_cos, similarity
from tesselint.render import render

# Upper triangle of a planted 6 x 6 similarity; 15 pairs, no ties, diagonal 1.0.
PAIR_SCORES = {
    (0, 1): 0.90,
    (0, 2): 0.88,
    (0, 3): 0.20,
    (0, 4): 0.15,
    (0, 5): 0.05,
    (1, 2): 0.85,
    (1, 3): 0.12,
    (1, 4): 0.08,
    (1, 5): 0.03,
    (2, 3): 0.80,
    (2, 4): 0.07,
    (2, 5): 0.02,
    (3, 4): 0.30,
    (3, 5): 0.01,
    (4, 5): 0.25,
}


def _planted() -> NDArray[np.float64]:
    sim = np.eye(6, dtype=np.float64)
    for (i, j), score in PAIR_SCORES.items():
        sim[i, j] = score
        sim[j, i] = score
    return sim


# Nearest neighbour per row: 0->1, 1->0, 2->0, 3->2, 4->3, 5->4. Second: 0->2, 1->2, 2->1,
# 3->4, 4->5, 5->0. Global top 20% of 15 pairs = 3 pairs: (0,1) .90, (0,2) .88, (1,2) .85.
@pytest.mark.parametrize(
    ("k", "top_pct", "expected"),
    [
        (1, 0.0, {(0, 1), (0, 2), (2, 3), (3, 4), (4, 5)}),
        (0, 20.0, {(0, 1), (0, 2), (1, 2)}),
        (1, 20.0, {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4), (4, 5)}),
        (2, 20.0, {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4), (4, 5), (0, 5)}),
    ],
    ids=["top-k-only", "global-only", "union-adds-global", "union-k2"],
)
def test_keep_pairs_is_top_k_union_global_top_pct(
    k: int, top_pct: float, expected: set[tuple[int, int]]
) -> None:
    """§2.2: kept = each story's k nearest others, plus the global top pct; (i < j), no diagonal."""
    kept = keep_pairs(_planted(), k=k, top_pct=top_pct)

    assert kept == expected
    assert all(i < j for i, j in kept)


@pytest.mark.slow
def test_real_fixture_keeps_both_known_pairs(real_section4: Path) -> None:
    """§2.2: on the real §4 full text, k=10 union top 2% keeps US-1.1 x US-20.1 and x US-20.2."""
    stories = parse_section(real_section4.read_text(encoding="utf-8")).stories
    ids = [s.id for s in stories]

    sim = similarity([render(s, "full") for s in stories])
    kept = keep_pairs(sim, k=10, top_pct=2.0)

    assert sim.shape == (len(ids), len(ids))
    np.testing.assert_allclose(sim, sim.T, atol=1e-6)
    i = ids.index("US-1.1")
    for other in ("US-20.1", "US-20.2"):
        j = ids.index(other)
        assert (min(i, j), max(i, j)) in kept, other


def test_similarity_takes_the_dot_product_in_float64(monkeypatch: pytest.MonkeyPatch) -> None:
    """A float32 product sums in the BLAS's own order, so its last bits differ between platforms."""
    import model2vec

    embs = np.random.default_rng(7).standard_normal((6, 64)).astype(np.float32)

    class Model:
        def encode(self, texts: list[str]) -> NDArray[np.float32]:
            return embs[: len(texts)]

    monkeypatch.setattr(model2vec.StaticModel, "from_pretrained", lambda *_a, **_k: Model())

    sim = similarity([f"story {i}" for i in range(6)])

    unit = (embs / np.linalg.norm(embs, axis=1, keepdims=True)).astype(np.float64)
    assert sim.dtype == np.float64
    assert np.array_equal(sim, unit @ unit.T)


@pytest.mark.parametrize(
    ("raw", "written"), [(0.20795007050037384, 0.208), (0.123456789, 0.1235), (0.5, 0.5)]
)
def test_written_cosines_have_at_most_4_decimals(raw: float, written: float) -> None:
    """Runs write and show cosines to 4 decimals; keep decisions still use the raw similarity."""
    assert rounded_cos(raw) == written
    assert len(repr(rounded_cos(raw)).split(".")[1]) <= 4
