"""Scheduler, retries, spend cap and aborts of `run_jobs`, with FakeJev at the HTTP boundary.

`sleep` and `rng` are injected, so backoff never waits and jitter is deterministic (DESIGN §2.7).
"""

import asyncio
import hashlib
import random
from contextlib import closing
from pathlib import Path

import pytest

from tesselint.cache import AnswerCache
from tesselint.jev import Answer, JevFatal
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.workers import (
    Job,
    JobResult,
    RetryPolicy,
    RunStats,
    SpendLedger,
    estimate_tokens,
    run_jobs,
)
from tests.fakes import (
    RESOLVED_MODEL,
    Failure,
    FakeJev,
    State,
    cost_for,
    http_error,
    input_tokens_for,
    missing_answers,
    missing_cost,
    state_key,
    timeout_error,
    transport_error,
)
from tests.helpers_pipeline import FAST_RATE, jev_client


class RecordingSleep:
    """Injected `sleep`: records each delay and returns at once."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(i: int, n_words: int = 12) -> str:
    return " ".join([f"Story{i}", *(["word"] * (n_words - 1))])


def atomic_job(i: int) -> Job:
    """An atomic.v2 job on a 12-word story; est_tokens equals what FakeJev will bill."""
    text = _text(i)
    return Job(
        key=_sha(f"atomic.v2|{text}"),
        a=f"US-1.{i}",
        b=None,
        order="fwd",
        state=text,
        question=ATOMIC_V2,
        est_tokens=input_tokens_for(text),
        text_hash_a=_sha(text),
        text_hash_b=None,
    )


def pair_job(i: int) -> Job:
    """A forward rel.v1 job on two 12-word stories."""
    text_a, text_b = _text(i), _text(1000 + i)
    state = {"A": text_a, "B": text_b}
    return Job(
        key=_sha(f"rel.v1|{text_a}|{text_b}"),
        a=f"US-1.{i}",
        b=f"US-2.{i}",
        order="fwd",
        state=state,
        question=REL_V1,
        est_tokens=input_tokens_for(state),
        text_hash_a=_sha(text_a),
        text_hash_b=_sha(text_b),
    )


def canned_answer(i: int, *, cost_usd: float = 1e-5) -> Answer:
    """An atomic.v2 answer FakeJev would never give (yes 0.42), to tell cache hits apart."""
    return Answer(
        question_id=ATOMIC_V2.id,
        choice=None,
        confidence=None,
        probabilities={"yes": 0.42},
        model=RESOLVED_MODEL,
        input_tokens=324,
        cost_usd=cost_usd,
        gen_id=f"gen-cached-{i}",
        latency_ms=250.0,
    )


async def serve(
    jobs: list[Job],
    fake: FakeJev,
    cache: AnswerCache,
    ledger: SpendLedger | None = None,
    *,
    concurrency: int = 4,
    sleep: RecordingSleep | None = None,
    seen: list[JobResult] | None = None,
) -> tuple[list[JobResult], RunStats]:
    """Call run_jobs through a real JevClient on `fake`, with injected sleep and rng."""
    async with jev_client(fake) as client:
        return await run_jobs(
            jobs,
            client,
            cache,
            ledger or SpendLedger(1.0),
            concurrency=concurrency,
            rate_per_s=FAST_RATE,
            on_result=(seen if seen is not None else []).append,
            sleep=sleep or RecordingSleep(),
            rng=random.Random(0),
        )


@pytest.mark.anyio
async def test_every_job_is_answered_once_and_cached(tmp_path: Path) -> None:
    """Each job gets exactly one call, one result, one on_result and a cache line (§2.6, §2.7)."""
    jobs = [pair_job(i) for i in range(10)] + [atomic_job(i) for i in range(10)]
    fake = FakeJev()
    seen: list[JobResult] = []

    with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
        results, stats = await serve(jobs, fake, cache, seen=seen)
        stored = {job.key: cache.get(job.key) for job in jobs}

    assert sorted(r.job.key for r in results) == sorted(job.key for job in jobs)
    assert sorted(r.job.key for r in seen) == sorted(job.key for job in jobs)
    assert set(fake.counts().values()) == {1}
    assert len(fake.calls) == len(jobs)
    assert all(r.answer is not None and not r.cached and r.attempts == 1 for r in results)
    assert all(r.answer.question_id == r.job.question.id for r in results if r.answer)
    assert {r.job.key: r.answer for r in results} == stored
    assert (stats.live, stats.cached, stats.failed, stats.retries) == (20, 0, 0, 0)
    assert stats.stopped == "complete"


@pytest.mark.anyio
async def test_cached_jobs_make_no_http_call(tmp_path: Path) -> None:
    """A job whose key is in the cache is served from it and counted as cached (DESIGN §2.6)."""
    jobs = [atomic_job(i) for i in range(10)]
    canned = {job.key: canned_answer(i) for i, job in enumerate(jobs[:4])}
    fake = FakeJev()

    with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
        for job in jobs[:4]:
            cache.put(job.key, canned[job.key], text_hash_a=job.text_hash_a, text_hash_b=None)
        results, stats = await serve(jobs, fake, cache)

    by_key = {r.job.key: r for r in results}
    assert {call.state for call in fake.calls} == {job.state for job in jobs[4:]}
    assert all(by_key[key].cached and by_key[key].answer == canned[key] for key in canned)
    assert (stats.live, stats.cached) == (6, 4)


@pytest.mark.anyio
@pytest.mark.parametrize("concurrency", [1, 4])
async def test_in_flight_calls_never_exceed_concurrency(tmp_path: Path, concurrency: int) -> None:
    """The semaphore caps calls in flight at `concurrency`, and the cap is used (DESIGN §2.7)."""
    jobs = [atomic_job(i) for i in range(16)]
    fake = FakeJev(latency_s=0.005)

    with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
        await serve(jobs, fake, cache, concurrency=concurrency)

    assert fake.max_in_flight == concurrency


RETRYABLE = [
    pytest.param(http_error(429, retry_after=3), 3.0, id="429-retry-after-3"),
    pytest.param(http_error(408), 0.0, id="408"),
    pytest.param(http_error(500), 0.0, id="500"),
    pytest.param(http_error(503, retry_after="7"), 7.0, id="503-retry-after-7"),
    pytest.param(http_error(402, in_flight=True), 0.0, id="402-in-flight"),
    pytest.param(transport_error(), 0.0, id="connect-error"),
    pytest.param(timeout_error(), 0.0, id="read-timeout"),
    pytest.param(missing_cost(), 0.0, id="200-without-usage-cost"),
    pytest.param(missing_answers(), 0.0, id="200-without-answers"),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("failure", "floor_s"), RETRYABLE)
async def test_retryable_failure_is_retried_after_a_backoff(
    tmp_path: Path, failure: Failure, floor_s: float
) -> None:
    """One retryable failure then success: 2 attempts, 1 retry, Retry-After as floor (§2.7)."""
    job = atomic_job(1)
    fake = FakeJev()
    fake.fail(failure)
    sleep = RecordingSleep()

    with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
        [result], stats = await serve([job], fake, cache, sleep=sleep)
        cached = cache.get(job.key)

    assert result.answer is not None and result.error is None
    assert result.attempts == 2
    assert fake.requests == 2
    assert (stats.live, stats.failed, stats.retries) == (1, 0, 1)
    assert sleep.delays, "the backoff must go through the injected sleep"
    assert floor_s <= max(sleep.delays) <= max(floor_s, 30.0)
    assert cached == result.answer


@pytest.mark.anyio
async def test_six_failed_attempts_leave_the_job_failed_and_uncached(tmp_path: Path) -> None:
    """After 6 retryable failures the job fails uncached; the rest completes (DESIGN §2.7)."""
    jobs = [atomic_job(i) for i in range(60)]  # 1 failed of 60 stays under the 2% abort
    doomed = jobs[-1]

    def is_doomed(state: State, question_id: str) -> bool:
        return state == doomed.state

    fake = FakeJev()
    fake.fail(*[http_error(503, when=is_doomed)] * 6)

    with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
        results, stats = await serve(jobs, fake, cache, concurrency=1)
        cached = cache.get(doomed.key)

    [failed] = [r for r in results if r.job.key == doomed.key]
    assert failed.answer is None and failed.error
    assert failed.attempts == 6
    assert fake.counts()[(state_key(doomed.state), ATOMIC_V2.id)] == 6
    assert cached is None
    assert (stats.live, stats.failed, stats.retries) == (59, 1, 5)
    assert stats.stopped == "complete"


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
async def test_fatal_status_is_not_retried_and_stops_the_run(tmp_path: Path, status: int) -> None:
    """Other 4xx (bad request, auth, credits) are fatal: one attempt, JevFatal raised (§2.7, §7)."""
    fake = FakeJev()
    fake.fail(http_error(status))

    with (
        closing(AnswerCache(tmp_path / "answers.jsonl")) as cache,
        pytest.raises(JevFatal) as raised,
    ):
        await serve([atomic_job(i) for i in range(3)], fake, cache, concurrency=1)

    assert raised.value.status == status
    assert fake.requests == 1


@pytest.mark.anyio
async def test_spend_cap_stops_dispatch_and_keeps_received_answers(tmp_path: Path) -> None:
    """At the cap dispatch stops (partial_cap), nothing more is called, answers persist (§2.7)."""
    jobs = [atomic_job(i) for i in range(12)]
    unit_usd = cost_for(jobs[0].state)  # every job costs the same and est_tokens is exact
    ledger = SpendLedger(cap_usd=5.5 * unit_usd)
    fake = FakeJev()
    path = tmp_path / "answers.jsonl"

    with closing(AnswerCache(path)) as cache:
        results, stats = await serve(jobs, fake, cache, ledger, concurrency=3)
        answered = [r for r in results if r.answer is not None]
        with closing(AnswerCache(path)) as reader:  # a second reader sees flushed lines
            persisted = [reader.get(r.job.key) for r in answered]

    assert fake.requests == 5
    assert len(answered) == 5
    assert (stats.live, stats.stopped) == (5, "partial_cap")
    assert ledger.spent_usd == pytest.approx(5 * unit_usd)
    assert persisted == [r.answer for r in answered]


@pytest.mark.anyio
async def test_resume_with_a_larger_cap_calls_only_missing_jobs(tmp_path: Path) -> None:
    """Re-running the same jobs on the same cache pays only for the missing answers (§2.7)."""
    jobs = [atomic_job(i) for i in range(12)]
    unit_usd = cost_for(jobs[0].state)
    fake = FakeJev()
    path = tmp_path / "answers.jsonl"
    with closing(AnswerCache(path)) as cache:
        first, _ = await serve(jobs, fake, cache, SpendLedger(5.5 * unit_usd), concurrency=3)
    answered = {r.job.key for r in first if r.answer is not None}
    calls_before = len(fake.calls)

    with closing(AnswerCache(path)) as cache:
        second, stats = await serve(jobs, fake, cache, SpendLedger(1.0), concurrency=3)

    resumed = {call.state for call in fake.calls[calls_before:]}
    assert resumed == {job.state for job in jobs if job.key not in answered}
    assert len(fake.calls) - calls_before == 7
    assert (stats.live, stats.cached, stats.stopped) == (7, 5, "complete")
    assert all(r.answer is not None for r in second) and len(second) == 12


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("n_jobs", "fail_every", "expected_failed"),
    [
        pytest.param(1100, 1, 20, id="20-consecutive-failures"),  # 20/1100 is under 2%
        pytest.param(100, 10, 3, id="over-2-percent-of-jobs"),  # 3/100 > 2%, never consecutive
    ],
)
async def test_too_many_failures_stop_with_partial_errors(
    tmp_path: Path, n_jobs: int, fail_every: int, expected_failed: int
) -> None:
    """20 consecutive failed jobs, or failed > 2% of all jobs, stops dispatch (DESIGN §2.7)."""
    jobs = [atomic_job(i) for i in range(n_jobs)]
    doomed = {str(jobs[i].state) for i in range(0, n_jobs, fail_every)}

    def is_doomed(state: State, question_id: str) -> bool:
        return isinstance(state, str) and state in doomed

    fake = FakeJev()
    fake.fail(*[http_error(500, when=is_doomed)] * (6 * (expected_failed + 5)))

    with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
        _, stats = await serve(jobs, fake, cache, concurrency=1)

    assert stats.stopped == "partial_errors"
    assert stats.failed == expected_failed
    assert stats.live + stats.failed < n_jobs


@pytest.mark.parametrize("attempt", [0, 1, 3, 5, 6, 10])
def test_retry_delay_is_full_jitter_under_the_cap(attempt: int) -> None:
    """delay = uniform(0, min(30, 0.5 * 2^attempt)), reproducible from the rng (DESIGN §2.7)."""
    policy = RetryPolicy()
    bound = min(30.0, 0.5 * 2**attempt)

    delays = [policy.delay(attempt, None, random.Random(i)) for i in range(200)]
    again = [policy.delay(attempt, None, random.Random(i)) for i in range(200)]

    assert all(0.0 <= d <= bound for d in delays)
    assert max(delays) > bound / 2  # jitter spreads over the interval, not a constant
    assert delays == again


@pytest.mark.parametrize(("attempt", "retry_after"), [(0, 5.0), (4, 5.0), (10, 45.0)])
def test_retry_delay_honours_retry_after_as_a_floor(attempt: int, retry_after: float) -> None:
    """The delay is at least Retry-After, even above the 30 s cap (DESIGN §2.7)."""
    policy = RetryPolicy()
    bound = max(retry_after, min(30.0, 0.5 * 2**attempt))

    delays = [policy.delay(attempt, retry_after, random.Random(i)) for i in range(200)]

    assert all(retry_after <= d <= bound for d in delays)


def test_spend_ledger_reserves_estimates_and_settles_real_costs() -> None:
    """Reservations count against the cap until settled; settle adds the real cost (§2.7)."""
    ledger = SpendLedger(cap_usd=1.0, usd_per_token=0.001)  # the cap is 1,000 tokens

    assert ledger.spent_usd == 0.0
    assert ledger.try_reserve(400)
    assert ledger.try_reserve(400)
    assert not ledger.try_reserve(300)  # 0.8 reserved + 0.3 > 1.0
    ledger.settle(400, canned_answer(1, cost_usd=0.1))  # real cost below the estimate
    assert ledger.spent_usd == pytest.approx(0.1)
    assert ledger.try_reserve(450)  # 0.1 spent + 0.4 + 0.45 reserved = 0.95
    ledger.settle(400, None)  # a failed call releases its reservation and costs nothing
    assert ledger.spent_usd == pytest.approx(0.1)
    assert ledger.try_reserve(400)  # 0.1 + 0.45 + 0.4 = 0.95
    assert not ledger.try_reserve(100)  # 1.05 > 1.0


@pytest.mark.parametrize(
    ("words_a", "words_b", "expected"),
    [
        (10, 20, 483 + 1.29 * 30),
        (175, 175, 483 + 1.29 * 350),
        (0, 0, 483.0),
        (26, None, 330 + 1.3 * 26),
        (736, None, 330 + 1.3 * 736),
    ],
)
def test_estimate_tokens_follows_the_token_model(
    words_a: int, words_b: int | None, expected: float
) -> None:
    """rel.v1 = 483 + 1.29 (words A + words B); atomic.v2 = 330 + 1.3 words (DESIGN §3)."""
    tokens = estimate_tokens(words_a, words_b)

    assert isinstance(tokens, int)
    assert abs(tokens - expected) < 1
