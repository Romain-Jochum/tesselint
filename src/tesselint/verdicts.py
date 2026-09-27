"""Forward/swap averaging into pair verdicts and the overlap matrix (DESIGN §2.5, §9)."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from tesselint.jobtypes import JobResult
from tesselint.models import Relation, Story
from tesselint.questions import REL_V1
from tesselint.render import render

REL_ORDER = (
    Relation.DUPLICATE,
    Relation.A_CONTAINS_B,
    Relation.B_CONTAINS_A,
    Relation.PARTIAL,
    Relation.SAME_AREA,
    Relation.UNRELATED,
)


@dataclass(frozen=True, slots=True)
class PairVerdict:
    """Combined rel.v1 answer for a pair; `a`, `b` in document order, probs in that frame."""

    a: str
    b: str
    probs: Mapping[Relation, float]
    fwd: Mapping[Relation, float]
    swap: Mapping[Relation, float] | None
    overlap: float
    merge: float
    relation: Relation


# One answered rel.v1 request: (story sent as A, story sent as B, order, probabilities as sent).
SentAnswer = tuple[str, str, str, Mapping[str, float]]

# o and m are sums of probabilities; rounding keeps 0.15 + 0.30 + 0.05 at 0.5 for the >= tests.
_DIGITS = 9


def build_verdicts(
    stories: Sequence[Story], results: Iterable[JobResult]
) -> dict[tuple[str, str], PairVerdict]:
    """Map rel.v1 results to document order, mirror swaps, average them, derive o and m (§2.5).

    Stories with identical texts get a local `duplicate` = 1.0 verdict when no answer covers the
    pair (§2.1: they are never sent to Jev).
    """
    answers = (
        (r.job.a, r.job.b, r.job.order, r.answer.probabilities)
        for r in results
        if r.answer is not None and r.job.question.id == REL_V1.id and r.job.b is not None
    )
    verdicts = verdicts_from_answers([s.id for s in stories], answers)

    by_text: dict[str, list[str]] = {}
    for s in stories:
        by_text.setdefault(render(s), []).append(s.id)
    for twins in by_text.values():
        for i, a in enumerate(twins):
            for b in twins[i + 1 :]:
                if (a, b) not in verdicts:
                    verdicts[(a, b)] = _verdict(a, b, {Relation.DUPLICATE: 1.0}, None)
    return verdicts


def verdicts_from_answers(
    ids: Sequence[str], answers: Iterable[SentAnswer]
) -> dict[tuple[str, str], PairVerdict]:
    """Combine answers into verdicts over `ids` (document order); pairs.jsonl rows fit too."""
    pos = {sid: i for i, sid in enumerate(ids)}
    grouped: dict[tuple[str, str], dict[str, dict[Relation, float]]] = {}

    for a_sent, b_sent, order, raw_probs in answers:
        if a_sent not in pos or b_sent not in pos or a_sent == b_sent:
            continue
        probs = {r: float(raw_probs.get(r.value, 0.0)) for r in Relation}
        if pos[a_sent] < pos[b_sent]:
            pair = (a_sent, b_sent)
        else:
            pair = (b_sent, a_sent)
            probs[Relation.A_CONTAINS_B], probs[Relation.B_CONTAINS_A] = (
                probs[Relation.B_CONTAINS_A],
                probs[Relation.A_CONTAINS_B],
            )
        grouped.setdefault(pair, {})[order] = probs

    verdicts: dict[tuple[str, str], PairVerdict] = {}
    for (doc_a, doc_b), orders in grouped.items():
        fwd = orders.get("fwd")
        swap = orders.get("swap")
        if fwd is None and swap is not None:
            fwd, swap = swap, None
        if fwd is None:
            continue
        verdicts[(doc_a, doc_b)] = _verdict(doc_a, doc_b, fwd, swap)
    return verdicts


def _verdict(
    a: str, b: str, fwd: Mapping[Relation, float], swap: Mapping[Relation, float] | None
) -> PairVerdict:
    """Average fwd with swap (if any) and derive o, m and the argmax relation (§2.5)."""
    fwd_full = {r: fwd.get(r, 0.0) for r in Relation}
    if swap is None:
        probs = fwd_full
    else:
        probs = {r: (fwd_full[r] + swap.get(r, 0.0)) / 2.0 for r in Relation}
    m = probs[Relation.DUPLICATE] + probs[Relation.A_CONTAINS_B] + probs[Relation.B_CONTAINS_A]
    o = m + probs[Relation.PARTIAL]
    return PairVerdict(
        a=a,
        b=b,
        probs=probs,
        fwd=fwd_full,
        swap=None if swap is None else {r: swap.get(r, 0.0) for r in Relation},
        overlap=round(o, _DIGITS),
        merge=round(m, _DIGITS),
        relation=max(REL_ORDER, key=lambda r: probs[r]),
    )


def overlap_matrix(
    ids: Sequence[str], verdicts: Mapping[tuple[str, str], PairVerdict]
) -> NDArray[np.float64]:
    """Return the symmetric overlap matrix over `ids`; NaN marks pairs not asked (DESIGN §9)."""
    n = len(ids)
    matrix = np.full((n, n), np.nan, dtype=np.float64)
    id_map = {sid: i for i, sid in enumerate(ids)}

    for (a, b), verdict in verdicts.items():
        if a in id_map and b in id_map:
            i, j = id_map[a], id_map[b]
            if i != j:
                matrix[i, j] = verdict.overlap
                matrix[j, i] = verdict.overlap

    return matrix
