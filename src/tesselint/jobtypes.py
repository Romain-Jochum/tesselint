"""Plain types of one Jev job and its answer (DESIGN §8; docs/ui/DESIGN.md §1, resolution 3).

Stdlib only: `verdicts`, `proposals`, `outputs` and `derive` import these instead of `workers`
or `jev`, so `tesselint.server` never loads `jev`, `httpx` or `dotenv`. `jev` re-exports
`Answer` and `workers` re-exports `Job`, `JobResult` and `RunStats` for compatibility.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from tesselint.models import Order
from tesselint.questions import Question


@dataclass(frozen=True, slots=True)
class Answer:
    """One parsed answer; `probabilities` is `{"yes": p}` for a noul question."""

    question_id: str
    choice: str | None
    confidence: float | None
    probabilities: Mapping[str, float]
    model: str
    input_tokens: int
    cost_usd: float
    gen_id: str
    latency_ms: float


@dataclass(frozen=True, slots=True)
class Job:
    """One question to ask about one story (b is None) or one ordered pair."""

    key: str
    a: str
    b: str | None
    order: Order
    state: str | Mapping[str, str]
    question: Question
    est_tokens: int
    text_hash_a: str
    text_hash_b: str | None


@dataclass(frozen=True, slots=True)
class JobResult:
    """Outcome of a Job: an answer (live or cached) or an error after retries."""

    job: Job
    answer: Answer | None
    cached: bool
    attempts: int
    error: str | None


@dataclass(frozen=True, slots=True)
class RunStats:
    """Call counts, latency and stop reason of one run_jobs call (DESIGN §9 run.json)."""

    live: int
    cached: int
    failed: int
    retries: int
    by_status: Mapping[str, int]
    wall_s: float
    p50_ms: float
    p95_ms: float
    stopped: Literal["complete", "partial_cap", "partial_errors", "interrupted"]
