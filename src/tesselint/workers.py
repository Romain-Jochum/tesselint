"""Scheduler, token bucket, retries and spend ledger (DESIGN §2.7, §8 workers.py)."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from aiolimiter import AsyncLimiter

from tesselint.cache import AnswerCache
from tesselint.jev import JevClient, JevFatal, JevRetryable
from tesselint.jobtypes import Answer

# Re-exported: the plain types moved to jobtypes so the server can import them without httpx.
from tesselint.jobtypes import Job as Job
from tesselint.jobtypes import JobResult as JobResult
from tesselint.jobtypes import RunStats as RunStats


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry schedule: 6 attempts, full-jitter exponential backoff capped at 30 s (DESIGN §2.7)."""

    max_attempts: int = 6
    base_s: float = 0.5
    cap_s: float = 30.0

    def delay(self, attempt: int, retry_after_s: float | None, rng: random.Random) -> float:
        """Return max(uniform(0, min(cap, base*2^attempt)), Retry-After) (DESIGN §2.7)."""
        upper = min(self.cap_s, self.base_s * (2**attempt))
        jittered = rng.uniform(0.0, upper)
        if retry_after_s is not None:
            return max(jittered, retry_after_s)
        return jittered


class SpendLedger:
    """Reserves each attempt's estimated cost before a call and settles what it cost after (§2.7).

    `spent_usd` counts every attempt that was or may have been billed: answers, rejected 200s that
    report `usage.cost`, and attempts such as read timeouts, charged at their estimate.
    `rejected_usd` is the part of it that bought no answer.
    """

    spent_usd: float

    def __init__(self, cap_usd: float, usd_per_token: float = 4.2e-8) -> None:
        """Start an empty ledger with the spend cap in USD (DESIGN §2.7)."""
        self.cap_usd = cap_usd
        self.usd_per_token = usd_per_token
        self.spent_usd = 0.0
        self.reserved_usd = 0.0
        self.rejected_usd = 0.0

    def try_reserve(self, est_tokens: int) -> bool:
        """Reserve est_tokens' cost if it fits under the cap; False means stop dispatch (§2.7)."""
        cost = est_tokens * self.usd_per_token
        if (self.spent_usd + self.reserved_usd + cost) - self.cap_usd > 1e-12:
            return False
        self.reserved_usd += cost
        return True

    def settle(self, est_tokens: int, answer: Answer | None, *, billed_usd: float = 0.0) -> None:
        """Release one attempt's reservation; add the answer's cost, or `billed_usd` without one."""
        cost = est_tokens * self.usd_per_token
        self.reserved_usd = max(0.0, self.reserved_usd - cost)
        if answer is not None:
            self.spent_usd += answer.cost_usd
        self.spent_usd += billed_usd
        self.rejected_usd += billed_usd


CAP_REACHED = "spend cap reached before a retry"
HALTED = "run stopped before a retry"


async def run_jobs(
    jobs: Sequence[Job],
    client: JevClient,
    cache: AnswerCache,
    ledger: SpendLedger,
    *,
    concurrency: int = 16,
    rate_per_s: float = 50.0,
    policy: RetryPolicy = RetryPolicy(),  # noqa: B008
    on_result: Callable[[JobResult], None] = lambda r: None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
    refresh: bool = False,
    status_counts: dict[str, int] | None = None,
) -> tuple[list[JobResult], RunStats]:
    """Serve jobs from cache or Jev with bucket, semaphore, retries, cap and aborts (§2.7).

    `refresh` skips cache reads (answers are still written). HTTP statuses are counted into
    `status_counts` when given, so a caller can merge phases and keep them when a run aborts.
    On JevFatal or any other error, dispatch and retries stop, calls in flight finish (their
    answers are cached and reported to `on_result`), then the first error is raised.
    """
    start_time = time.perf_counter()
    active_rng = random.Random() if rng is None else rng
    limiter = AsyncLimiter(rate_per_s, 1.0)
    sem = asyncio.Semaphore(concurrency)
    by_status: dict[str, int] = {} if status_counts is None else status_counts

    live_count = cached_count = failed_count = retries_count = consecutive_failures = 0
    latencies: list[float] = []
    stop_reason: Literal["complete", "partial_cap", "partial_errors", "interrupted"] = "complete"
    results: list[JobResult] = []
    in_flight: set[asyncio.Task[JobResult]] = set()
    calling: set[asyncio.Task[Any] | None] = set()  # tasks inside client.decide: not cancelled
    halted = False
    abort: BaseException | None = None

    def count_status(status: int | None) -> None:
        if status is not None:
            by_status[str(status)] = by_status.get(str(status), 0) + 1

    def billed_usd(job: Job, error: JevRetryable) -> float:
        if error.cost_usd is not None:
            return error.cost_usd
        return job.est_tokens * ledger.usd_per_token if error.maybe_billed else 0.0

    async def attempt(job: Job) -> Answer | JevRetryable:
        """One reserved call; settles the ledger whatever happens, queued or sent."""
        task = asyncio.current_task()
        sent = False
        try:
            async with sem, limiter:
                calling.add(task)
                try:
                    sent = True
                    answer = await client.decide(job.state, job.question)
                finally:
                    calling.discard(task)
        except JevRetryable as e:
            ledger.settle(job.est_tokens, None, billed_usd=billed_usd(job, e))
            count_status(e.status)
            return e
        except JevFatal as e:
            ledger.settle(job.est_tokens, None)
            count_status(e.status)
            raise
        except asyncio.CancelledError:  # Ctrl-C: a call already sent may have been billed
            maybe = job.est_tokens * ledger.usd_per_token if sent else 0.0
            ledger.settle(job.est_tokens, None, billed_usd=maybe)
            raise
        except BaseException:
            ledger.settle(job.est_tokens, None)
            raise
        ledger.settle(job.est_tokens, answer)
        count_status(200)
        latencies.append(answer.latency_ms)
        return answer

    async def execute_job(job: Job) -> JobResult:
        """Attempt a job (its first reservation is made at dispatch) until it succeeds or fails."""
        nonlocal retries_count
        error = ""
        for attempt_no in range(1, policy.max_attempts + 1):
            if attempt_no > 1:
                if halted:
                    return JobResult(job, None, False, attempt_no - 1, f"{error}; {HALTED}")
                if not ledger.try_reserve(job.est_tokens):
                    return JobResult(job, None, False, attempt_no - 1, CAP_REACHED)
                retries_count += 1
            outcome = await attempt(job)
            if isinstance(outcome, Answer):
                cache.put(
                    job.key,
                    outcome,
                    text_hash_a=job.text_hash_a,
                    text_hash_b=job.text_hash_b,
                    question_hash=job.question.hash,
                    model_requested=client.model,
                )
                return JobResult(job, outcome, False, attempt_no, None)
            error = str(outcome)
            if attempt_no < policy.max_attempts:
                await sleep(policy.delay(attempt_no - 1, outcome.retry_after_s, active_rng))
        return JobResult(job, None, False, policy.max_attempts, error)

    def record(res: JobResult) -> None:
        nonlocal live_count, cached_count, failed_count, consecutive_failures, stop_reason
        results.append(res)
        on_result(res)
        if res.cached:
            cached_count += 1
        elif res.answer is not None:
            live_count += 1
            consecutive_failures = 0
        else:
            failed_count += 1
            consecutive_failures += 1
            if res.error == CAP_REACHED and stop_reason == "complete":
                stop_reason = "partial_cap"

    def too_many_failures() -> bool:
        over_share = len(jobs) > 0 and failed_count / len(jobs) > 0.02
        return consecutive_failures >= 20 or over_share

    job_idx = 0
    try:
        while True:
            while (
                abort is None
                and stop_reason == "complete"
                and job_idx < len(jobs)
                and len(in_flight) < concurrency
            ):
                job = jobs[job_idx]
                job_idx += 1
                cached_ans = None if refresh else cache.get(job.key)
                if cached_ans is not None:
                    record(JobResult(job, cached_ans, True, 0, None))
                    continue
                if not ledger.try_reserve(job.est_tokens):
                    stop_reason = "partial_cap"
                    break
                in_flight.add(asyncio.create_task(execute_job(job)))

            if not in_flight:
                break
            done, in_flight = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            for finished in done:
                if finished.cancelled():
                    continue  # a job cancelled between retries after an abort
                exc = finished.exception()
                if exc is None:
                    record(finished.result())
                elif abort is None:
                    abort, halted = exc, True
                    for task in in_flight - calling:
                        task.cancel()
            if stop_reason == "complete" and too_many_failures():
                stop_reason = "partial_errors"
    except BaseException:  # Ctrl-C (CancelledError) or a failing on_result: leave no task behind
        for task in in_flight:
            task.cancel()
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        raise
    if abort is not None:
        raise abort

    wall_s = time.perf_counter() - start_time
    stats = RunStats(
        live=live_count,
        cached=cached_count,
        failed=failed_count,
        retries=retries_count,
        by_status=dict(by_status),
        wall_s=wall_s,
        p50_ms=float(np.percentile(latencies, 50)) if latencies else 0.0,
        p95_ms=float(np.percentile(latencies, 95)) if latencies else 0.0,
        stopped=stop_reason,
    )
    return results, stats


def estimate_tokens(words_a: int, words_b: int | None) -> int:
    """Estimate input tokens of one call from word counts, for the preflight cap (DESIGN §2.7)."""
    if words_b is not None:
        return round(483 + 1.29 * (words_a + words_b))
    return round(330 + 1.3 * words_a)
