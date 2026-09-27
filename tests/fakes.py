"""FakeJev: an httpx.MockTransport that stands in for OpenRouter's Jev endpoints.

Routes (any host; paths as under the default base URL `https://openrouter.ai/api`):

- `POST /api/alpha/decisions` takes `{"model", "state", "questions": {key: payload}}` and answers
  every question under the key it was sent with, in the real body shape (see jev_bodies.json).
- `GET /api/v1/key` returns `{"data": {..., "usage": key_usage_start + sum of costs served}}`.

A question is identified by its payload hash (REL_V1 -> "rel.v1", ATOMIC_V2 -> "atomic.v2");
an unknown payload is identified by its request key. The answer function receives
`(state, question_id)` and returns probabilities: `{"yes": p}` for a noul question, or
`{option: p}` for a choice question (missing options are 0; the choice is the argmax, ties go to
the earlier option; confidence is top - 0.02). Probabilities are rounded to 2 dp, as Jev does.

Tokens and cost are deterministic: `input_tokens = 300 + 2 * words(state)` and
`cost = input_tokens * 4.2e-8` (see `input_tokens_for`, `cost_for`).

Failures are scripted with `fake.fail(...)`: each request takes the first queued failure whose
`when(state, question_id)` matches (or that has no `when`); an empty queue means success.
"""

import asyncio
import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from tesselint.questions import ATOMIC_V2, REL_V1, Question

State = str | Mapping[str, str]
StateKey = str | tuple[tuple[str, str], ...] | frozenset[str]
AnswerFn = Callable[[State, str], Mapping[str, float]]
Predicate = Callable[[State, str], bool]
FailureKind = Literal["http", "transport", "timeout", "missing_cost", "missing_answers"]

DECISIONS_PATH = "/api/alpha/decisions"
KEY_PATH = "/api/v1/key"
RESOLVED_MODEL = "typesafe/jev-1.13-20260917"
USD_PER_TOKEN = 4.2e-8
PROMPT_OVERHEAD_TOKENS = 300
TOKENS_PER_WORD = 2
OUTPUT_TOKENS: Mapping[str, int] = {"choice": 72, "noul": 20}
KNOWN_QUESTIONS: Mapping[str, str] = {q.hash: q.id for q in (REL_V1, ATOMIC_V2)}
IN_FLIGHT_LIMIT_SOURCE = "openrouter_in_flight_budget"

_ERROR_MESSAGES: Mapping[int, str] = {
    400: 'HTTP 400: {"detail":"Bad request"}',
    401: "No auth credentials found",
    402: "Insufficient credits. Add more using https://openrouter.ai/settings/credits",
    404: "Not Found",
    408: "Request timed out",
    429: "Rate limit exceeded",
}


def words(state: State) -> int:
    """Whitespace word count of a story text, or of both texts of a pair."""
    if isinstance(state, str):
        return len(state.split())
    return sum(len(text.split()) for text in state.values())


def input_tokens_for(state: State) -> int:
    """Input tokens FakeJev reports for one request with this state."""
    return PROMPT_OVERHEAD_TOKENS + TOKENS_PER_WORD * words(state)


def cost_for(state: State) -> float:
    """`usage.cost` FakeJev reports for one request with this state."""
    return input_tokens_for(state) * USD_PER_TOKEN


def state_key(state: State, *, unordered: bool = False) -> StateKey:
    """Hashable form of a state; `unordered` makes {"A": x, "B": y} equal {"A": y, "B": x}."""
    if isinstance(state, str):
        return state
    if unordered:
        return frozenset(state.values())
    return tuple(sorted(state.items()))


@dataclass(frozen=True, slots=True)
class Failure:
    """One scripted failure; build it with the helpers below."""

    kind: FailureKind
    status: int = 200
    retry_after: str | None = None
    in_flight: bool = False
    when: Predicate | None = None


def http_error(
    status: int,
    *,
    retry_after: float | str | None = None,
    in_flight: bool = False,
    when: Predicate | None = None,
) -> Failure:
    """An HTTP error; `in_flight=True` on a 402 marks the retryable in-flight-budget variant."""
    header = None if retry_after is None else str(retry_after)
    return Failure("http", status=status, retry_after=header, in_flight=in_flight, when=when)


def transport_error(when: Predicate | None = None) -> Failure:
    """The request raises httpx.ConnectError."""
    return Failure("transport", when=when)


def timeout_error(when: Predicate | None = None) -> Failure:
    """The request raises httpx.ReadTimeout."""
    return Failure("timeout", when=when)


def missing_cost(when: Predicate | None = None) -> Failure:
    """A 200 whose `usage` lacks `cost` (not added to the key usage)."""
    return Failure("missing_cost", when=when)


def missing_answers(when: Predicate | None = None) -> Failure:
    """A 200 without `answers` (its cost is still charged)."""
    return Failure("missing_answers", when=when)


@dataclass(frozen=True, slots=True)
class FakeCall:
    """One question of one decisions request; outcome is "ok", "http_<status>" or a kind."""

    state: State
    question_id: str
    key: str
    model: str
    outcome: str


def default_answer(state: State, question_id: str) -> Mapping[str, float]:
    """Unrelated pairs and atomic stories."""
    if question_id == REL_V1.id:
        return {"unrelated": 0.9, "same_area_distinct": 0.1}
    return {"yes": 0.1}


def _error_body(status: int, *, in_flight: bool = False) -> dict[str, Any]:
    message = _ERROR_MESSAGES.get(status, "Internal Server Error" if status >= 500 else "Error")
    error: dict[str, Any] = {"message": message, "code": status}
    if status == 402 and in_flight:
        error["message"] = "Too many requests in flight for this key"
        error["metadata"] = {"limit_source": IN_FLIGHT_LIMIT_SOURCE}
    return {"error": error}


class FakeJev:
    """In-memory Jev. Pass `fake.transport` to JevClient(..., transport=fake.transport)."""

    def __init__(
        self,
        answer: AnswerFn | None = None,
        *,
        latency_s: float = 0.0,
        resolved_model: str = RESOLVED_MODEL,
        api_key: str | None = None,
        key_usage_start: float = 0.0,
        key_limit: float = 2.0,
        key_status: int = 200,
    ) -> None:
        self.answer: AnswerFn = answer or default_answer
        self.latency_s = latency_s
        self.resolved_model = resolved_model
        self.api_key = api_key
        self.key_usage_start = key_usage_start
        self.key_limit = key_limit
        self.key_status = key_status
        self.calls: list[FakeCall] = []
        self.failures: list[Failure] = []
        self.requests = 0
        self.key_requests = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.total_cost = 0.0
        self.transport = httpx.MockTransport(self._handle)

    def fail(self, *failures: Failure) -> None:
        """Queue failures, consumed in order by matching requests."""
        self.failures.extend(failures)

    def counts(
        self, *, outcome: str | None = None, unordered: bool = False
    ) -> Counter[tuple[StateKey, str]]:
        """Attempts per (state_key, question_id), optionally only those with `outcome`."""
        return Counter(
            (state_key(call.state, unordered=unordered), call.question_id)
            for call in self.calls
            if outcome is None or call.outcome == outcome
        )

    def ok_calls(self) -> list[FakeCall]:
        """Calls that got a well-formed 200."""
        return [call for call in self.calls if call.outcome == "ok"]

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == KEY_PATH:
            return self._key(request)
        if request.method == "POST" and path == DECISIONS_PATH:
            return await self._decide(request)
        return httpx.Response(404, json=_error_body(404))

    def _authorised(self, request: httpx.Request) -> bool:
        header: str = request.headers.get("authorization", "")
        if self.api_key is None:
            return header.startswith("Bearer ") and len(header) > len("Bearer ")
        return header == f"Bearer {self.api_key}"

    def _key(self, request: httpx.Request) -> httpx.Response:
        self.key_requests += 1
        if not self._authorised(request):
            return httpx.Response(401, json=_error_body(401))
        if self.key_status != 200:
            return httpx.Response(self.key_status, json=_error_body(self.key_status))
        usage = self.key_usage_start + self.total_cost
        data = {
            "limit": self.key_limit,
            "limit_remaining": self.key_limit - usage,
            "usage": usage,
            "usage_daily": usage,
            "usage_weekly": usage,
            "usage_monthly": usage,
            "byok_usage": 0,
            "byok_usage_daily": 0,
            "byok_usage_weekly": 0,
            "byok_usage_monthly": 0,
            "rate_limit": {
                "requests": -1,
                "interval": "10s",
                "note": "This field is deprecated and safe to ignore.",
            },
        }
        return httpx.Response(200, json={"data": data})

    async def _decide(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.latency_s)
            return self._respond(request)
        finally:
            self.in_flight -= 1

    def _respond(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state: State = body["state"]
        model: str = body.get("model", "")
        questions: Mapping[str, Mapping[str, Any]] = body["questions"]
        ids = {key: self._question_id(key, payload) for key, payload in questions.items()}

        def log(outcome: str) -> None:
            self.calls.extend(FakeCall(state, qid, key, model, outcome) for key, qid in ids.items())

        if not self._authorised(request):
            log("http_401")
            return httpx.Response(401, json=_error_body(401))
        failure = self._take_failure(state, next(iter(ids.values()), ""))
        if failure is not None and failure.kind == "transport":
            log("transport")
            raise httpx.ConnectError("fake connect error", request=request)
        if failure is not None and failure.kind == "timeout":
            log("timeout")
            raise httpx.ReadTimeout("fake read timeout", request=request)
        if failure is not None and failure.kind == "http":
            log(f"http_{failure.status}")
            headers = {} if failure.retry_after is None else {"Retry-After": failure.retry_after}
            error = _error_body(failure.status, in_flight=failure.in_flight)
            return httpx.Response(failure.status, json=error, headers=headers)

        answers: dict[str, Any] = {}
        output_tokens = 0
        for key, payload in questions.items():
            qtype = str(payload.get("type"))
            if qtype not in OUTPUT_TOKENS:
                log("http_400")
                return httpx.Response(400, json=_error_body(400))
            answers[key] = self._answer_body(payload, self.answer(state, ids[key]))
            output_tokens += OUTPUT_TOKENS[qtype]
        tokens = input_tokens_for(state)
        cost = tokens * USD_PER_TOKEN
        usage: dict[str, Any] = {
            "input_tokens": tokens,
            "output_tokens": output_tokens,
            "cost": cost,
        }
        response: dict[str, Any] = {
            "model": self.resolved_model,
            "answers": answers,
            "usage": usage,
            "id": f"gen-dec-1790000000-FAKE{self.requests:016d}",
            "provider": "TypeSafe",
        }
        outcome = "ok"
        if failure is not None and failure.kind == "missing_cost":
            del usage["cost"]
            cost = 0.0
            outcome = "missing_cost"
        if failure is not None and failure.kind == "missing_answers":
            del response["answers"]
            outcome = "missing_answers"
        self.total_cost += cost
        log(outcome)
        return httpx.Response(200, json=response)

    def _take_failure(self, state: State, question_id: str) -> Failure | None:
        for i, failure in enumerate(self.failures):
            if failure.when is None or failure.when(state, question_id):
                return self.failures.pop(i)
        return None

    @staticmethod
    def _question_id(key: str, payload: Mapping[str, Any]) -> str:
        return KNOWN_QUESTIONS.get(Question(key, payload).hash, key)

    @staticmethod
    def _answer_body(payload: Mapping[str, Any], probs: Mapping[str, float]) -> dict[str, Any]:
        if payload["type"] == "noul":
            return {"type": "noul", "noul": round(probs["yes"], 2)}
        options = list(payload["criteria"])
        unknown = set(probs) - set(options)
        if unknown:
            raise ValueError(f"answer function returned unknown options: {sorted(unknown)}")
        rounded = {option: round(probs.get(option, 0.0), 2) for option in options}
        choice = max(options, key=lambda option: rounded[option])
        confidence = round(max(0.0, rounded[choice] - 0.02), 2)
        return {
            "type": "choice",
            "choice": choice,
            "probabilities": rounded,
            "confidence": confidence,
        }
