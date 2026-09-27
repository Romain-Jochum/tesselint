"""verdicts: forward/swap averaging in document order, o and m, and the overlap matrix.

Notation (DESIGN header): o = P(duplicate) + P(A_contains_B) + P(B_contains_A) + P(partial_overlap)
and m = o - P(partial_overlap). A relation missing from an answer's probabilities counts as 0.
`PairVerdict.fwd` and `.swap` are expressed in document order like `.probs` (DESIGN §9 evidence:
"fwd partial 0.53 / A⊃B 0.37 · swap A⊃B 0.66" for US-1.1 before US-20.2).
"""

from collections.abc import Mapping

import numpy as np
import pytest

from tesselint.jev import Answer
from tesselint.models import Order, Relation, Story
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.verdicts import PairVerdict, build_verdicts, overlap_matrix
from tesselint.workers import Job, JobResult

R = Relation
MODEL = "typesafe/jev-1.13-20260917"
NAN = float("nan")


def _story(sid: str, line: int) -> Story:
    return Story(
        id=sid,
        epic="E1",
        title=f"Title {line}",
        tag="MVP",
        core="I do one thing on my plot, so that the site runs well.",
        subs=(),
        line=line,
    )


# Document order is the order of this tuple, not ID order.
STORIES = (_story("US-1.1", 10), _story("US-1.2", 20), _story("US-1.3", 30))


def _answer(question_id: str, probs: Mapping[str, float]) -> Answer:
    best = max(probs, key=lambda k: probs[k])
    return Answer(
        question_id=question_id,
        choice=best,
        confidence=probs[best],
        probabilities=dict(probs),
        model=MODEL,
        input_tokens=600,
        cost_usd=2.5e-5,
        gen_id="gen-test",
        latency_ms=300.0,
    )


def _rel(sent_a: str, sent_b: str, order: Order, probs: Mapping[Relation, float]) -> JobResult:
    """A rel.v1 result for the pair as sent: `sent_a` was story A in the request."""
    job = Job(
        key=f"{order}:{sent_a}:{sent_b}",
        a=sent_a,
        b=sent_b,
        order=order,
        state={"A": f"text of {sent_a}", "B": f"text of {sent_b}"},
        question=REL_V1,
        est_tokens=600,
        text_hash_a=f"hash-{sent_a}",
        text_hash_b=f"hash-{sent_b}",
    )
    answer = _answer(REL_V1.id, {r.value: p for r, p in probs.items()})
    return JobResult(job=job, answer=answer, cached=False, attempts=1, error=None)


def _mirror(probs: Mapping[Relation, float]) -> dict[Relation, float]:
    """The same judgement seen from the other order: only the containments swap."""
    flip = {R.A_CONTAINS_B: R.B_CONTAINS_A, R.B_CONTAINS_A: R.A_CONTAINS_B}
    return {flip.get(r, r): p for r, p in probs.items()}


def _full(probs: Mapping[Relation, float]) -> dict[Relation, float]:
    return {r: probs.get(r, 0.0) for r in Relation}


@pytest.mark.parametrize(
    ("fwd", "o", "m", "relation"),
    [
        (
            {
                R.DUPLICATE: 0.1,
                R.A_CONTAINS_B: 0.2,
                R.B_CONTAINS_A: 0.05,
                R.PARTIAL: 0.3,
                R.SAME_AREA: 0.25,
                R.UNRELATED: 0.1,
            },
            0.65,
            0.35,
            R.PARTIAL,
        ),
        ({R.DUPLICATE: 1.0}, 1.0, 1.0, R.DUPLICATE),
        ({R.PARTIAL: 0.9, R.UNRELATED: 0.1}, 0.9, 0.0, R.PARTIAL),
        ({R.SAME_AREA: 0.6, R.UNRELATED: 0.4}, 0.0, 0.0, R.SAME_AREA),
    ],
    ids=["mixed", "duplicate", "partial", "same-area"],
)
def test_forward_only_verdict_is_the_forward_answer(
    fwd: dict[Relation, float], o: float, m: float, relation: Relation
) -> None:
    """§2.5 verdict: with no swap, probs = fwd; o and m follow the DESIGN notation."""
    verdicts = build_verdicts(STORIES, [_rel("US-1.1", "US-1.2", "fwd", fwd)])

    assert set(verdicts) == {("US-1.1", "US-1.2")}
    v = verdicts[("US-1.1", "US-1.2")]
    assert isinstance(v, PairVerdict)
    assert (v.a, v.b) == ("US-1.1", "US-1.2")
    assert _full(v.probs) == pytest.approx(_full(fwd))
    assert _full(v.fwd) == pytest.approx(_full(fwd))
    assert v.swap is None
    assert v.overlap == pytest.approx(o)
    assert v.merge == pytest.approx(m)
    assert v.relation == relation


@pytest.mark.parametrize(
    ("sent", "in_doc_order"),
    [
        (R.A_CONTAINS_B, R.B_CONTAINS_A),
        (R.B_CONTAINS_A, R.A_CONTAINS_B),
        (R.DUPLICATE, R.DUPLICATE),
        (R.PARTIAL, R.PARTIAL),
    ],
)
def test_forward_sent_later_story_as_a_is_expressed_in_document_order(
    sent: Relation, in_doc_order: Relation
) -> None:
    """§2.1/§2.5: forward A is the smaller text hash, so it may be the later story; flip it back."""
    fwd_as_sent = {sent: 0.7, R.SAME_AREA: 0.3}

    verdicts = build_verdicts(STORIES, [_rel("US-1.2", "US-1.1", "fwd", fwd_as_sent)])

    assert set(verdicts) == {("US-1.1", "US-1.2")}
    v = verdicts[("US-1.1", "US-1.2")]
    assert (v.a, v.b) == ("US-1.1", "US-1.2")
    expected = _full({in_doc_order: 0.7, R.SAME_AREA: 0.3})
    assert _full(v.probs) == pytest.approx(expected)
    assert _full(v.fwd) == pytest.approx(expected)
    assert v.relation == in_doc_order


@pytest.mark.parametrize(
    "fwd_sent_in_doc_order", [True, False], ids=["fwd-1.1-first", "fwd-1.2-first"]
)
def test_swap_is_mirrored_back_and_averaged(fwd_sent_in_doc_order: bool) -> None:
    """§2.3/§2.5: the reversed answer maps A_contains_B <-> B_contains_A, then averages with fwd."""
    fwd_doc = {R.DUPLICATE: 0.1, R.A_CONTAINS_B: 0.4, R.PARTIAL: 0.3, R.SAME_AREA: 0.2}
    swap_doc = {R.DUPLICATE: 0.2, R.A_CONTAINS_B: 0.6, R.PARTIAL: 0.2}
    if fwd_sent_in_doc_order:
        results = [
            _rel("US-1.1", "US-1.2", "fwd", fwd_doc),
            _rel("US-1.2", "US-1.1", "swap", _mirror(swap_doc)),
        ]
    else:
        results = [
            _rel("US-1.2", "US-1.1", "fwd", _mirror(fwd_doc)),
            _rel("US-1.1", "US-1.2", "swap", swap_doc),
        ]

    v = build_verdicts(STORIES, results)[("US-1.1", "US-1.2")]

    assert _full(v.fwd) == pytest.approx(_full(fwd_doc))
    assert v.swap is not None
    assert _full(v.swap) == pytest.approx(_full(swap_doc))
    assert _full(v.probs) == pytest.approx(
        _full({R.DUPLICATE: 0.15, R.A_CONTAINS_B: 0.5, R.PARTIAL: 0.25, R.SAME_AREA: 0.1})
    )
    assert v.overlap == pytest.approx(0.9)
    assert v.merge == pytest.approx(0.65)
    assert v.relation == R.A_CONTAINS_B


def test_atomic_and_failed_results_make_no_verdict() -> None:
    """§2.5: only answered rel.v1 pairs become verdicts; atomic.v2 and failed jobs are skipped."""
    atomic_job = Job(
        key="atomic:US-1.3",
        a="US-1.3",
        b=None,
        order="fwd",
        state="text of US-1.3",
        question=ATOMIC_V2,
        est_tokens=400,
        text_hash_a="hash-US-1.3",
        text_hash_b=None,
    )
    atomic = JobResult(
        job=atomic_job,
        answer=_answer(ATOMIC_V2.id, {"yes": 0.9}),
        cached=True,
        attempts=0,
        error=None,
    )
    failed = _rel("US-1.1", "US-1.3", "fwd", {R.PARTIAL: 1.0})
    failed = JobResult(job=failed.job, answer=None, cached=False, attempts=6, error="HTTP 500")
    answered = _rel("US-1.1", "US-1.2", "fwd", {R.PARTIAL: 0.8, R.UNRELATED: 0.2})

    verdicts = build_verdicts(STORIES, [atomic, failed, answered])

    assert set(verdicts) == {("US-1.1", "US-1.2")}


def _pv(a: str, b: str, o: float) -> PairVerdict:
    probs = {R.PARTIAL: o, R.UNRELATED: 1.0 - o}
    return PairVerdict(
        a=a, b=b, probs=probs, fwd=probs, swap=None, overlap=o, merge=0.0, relation=R.PARTIAL
    )


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        (
            ["US-1.1", "US-1.2", "US-1.3"],
            [[NAN, 0.9, NAN], [0.9, NAN, 0.3], [NAN, 0.3, NAN]],
        ),
        (
            ["US-1.3", "US-1.2", "US-1.1"],
            [[NAN, 0.3, NAN], [0.3, NAN, 0.9], [NAN, 0.9, NAN]],
        ),
    ],
    ids=["document-order", "reversed"],
)
def test_overlap_matrix_is_symmetric_with_nan_where_never_asked(
    ids: list[str], expected: list[list[float]]
) -> None:
    """§8/§9 matrix.csv: square in `ids` order, o both ways; NaN = not asked, incl. diagonal."""
    verdicts = {
        ("US-1.1", "US-1.2"): _pv("US-1.1", "US-1.2", 0.9),
        ("US-1.2", "US-1.3"): _pv("US-1.2", "US-1.3", 0.3),
    }

    matrix = overlap_matrix(ids, verdicts)

    assert matrix.shape == (3, 3)
    assert matrix.dtype == np.float64
    np.testing.assert_allclose(matrix, np.array(expected), equal_nan=True)
