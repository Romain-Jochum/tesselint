"""Jev HTTP client: response parsing, request shape, status classification, key hygiene.

DESIGN §2.7 (retry classification, cost), §7 (the key is never logged), §8 jev.py. The only fake
is FakeJev at the HTTP boundary (httpx.MockTransport); JevClient itself is real.
"""

import logging
import traceback
from collections.abc import Mapping
from contextlib import aclosing
from typing import Any

import pytest

from tesselint.jev import Answer, JevClient, JevError, JevFatal, JevRetryable, parse_response
from tesselint.questions import ATOMIC_V2, REL_V1, Question
from tests.fakes import (
    RESOLVED_MODEL,
    Failure,
    FakeJev,
    cost_for,
    http_error,
    input_tokens_for,
    missing_answers,
    missing_cost,
    timeout_error,
    transport_error,
)

DUMMY_KEY = "sk-or-v1-DUMMY-leakcheck-7f3a9c1e5b"
MODEL = "typesafe/jev-1.13"
PAIR = {
    "A": "Tool catalogue. The shed lists every tool.",
    "B": "Tool search. I find a tool by name.",
}
STORY = "Rate limits. Two-hour slots for every power tool."


def _client(fake: FakeJev, api_key: str = DUMMY_KEY, model: str = MODEL) -> JevClient:
    return JevClient(api_key, model=model, transport=fake.transport)


# --- parse_response on recorded bodies ------------------------------------------------------------


@pytest.mark.parametrize(
    ("body_name", "question", "expected"),
    [
        (
            "choice_rel",
            REL_V1,
            Answer(
                question_id="rel.v1",
                choice="same_area_distinct",
                confidence=0.84,
                probabilities={
                    "same_area_distinct": 0.86,
                    "unrelated": 0.07,
                    "A_contains_B": 0.01,
                    "partial_overlap": 0.06,
                    "B_contains_A": 0.0,
                    "duplicate": 0.0,
                },
                model="typesafe/jev-1.13-20260917",
                input_tokens=602,
                cost_usd=2.5284e-05,
                gen_id="gen-dec-1790422720-FIXTURE0000000000001",
                latency_ms=301.0,
            ),
        ),
        (
            "multi_rel_related_sim",
            REL_V1,
            Answer(
                question_id="rel.v1",
                choice="same_area_distinct",
                confidence=0.85,
                probabilities={
                    "partial_overlap": 0.06,
                    "B_contains_A": 0.0,
                    "unrelated": 0.06,
                    "A_contains_B": 0.01,
                    "same_area_distinct": 0.87,
                    "duplicate": 0.0,
                },
                model="typesafe/jev-1.13-20260917",
                input_tokens=711,
                cost_usd=2.9862e-05,
                gen_id="gen-dec-1790422722-FIXTURE0000000000003",
                latency_ms=301.0,
            ),
        ),
        (
            "noul_atomic_v2",
            ATOMIC_V2,
            Answer(
                question_id="atomic.v2",
                choice=None,
                confidence=None,
                probabilities={"yes": 0.73},
                model="typesafe/jev-1.13-20260917",
                input_tokens=468,
                cost_usd=1.9656e-05,
                gen_id="gen-dec-0000000000-FIXTURE0000000000005",
                latency_ms=301.0,
            ),
        ),
        (
            "noul_atomic_v1",
            ATOMIC_V2,
            Answer(
                question_id="atomic.v2",
                choice=None,
                confidence=None,
                probabilities={"yes": 0.22},
                model="typesafe/jev-1.13-20260917",
                input_tokens=309,
                cost_usd=1.2978e-05,
                gen_id="gen-dec-1790422716-FIXTURE0000000000002",
                latency_ms=301.0,
            ),
        ),
    ],
    ids=["choice", "choice-among-three-answers", "noul-v2", "noul-v1-body"],
)
def test_parse_response_on_recorded_bodies(
    jev_bodies: dict[str, Any], body_name: str, question: Question, expected: Answer
) -> None:
    """DESIGN §8 parse_response: the answer under the question's key, usage, model and id."""
    assert parse_response(jev_bodies[body_name], question, 301.0) == expected


@pytest.mark.parametrize("missing", ["answers", "usage.cost"])
def test_parse_response_without_answers_or_cost_is_retryable(
    jev_bodies: dict[str, Any], missing: str
) -> None:
    """DESIGN §2.7: a 200 lacking `usage.cost` or `answers` is retried."""
    body = jev_bodies["choice_rel"]
    if missing == "answers":
        del body["answers"]
    else:
        del body["usage"]["cost"]

    with pytest.raises(JevRetryable):
        parse_response(body, REL_V1, 301.0)


# --- decide: request shape and parsed answer ------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("state", "question", "probs", "key", "choice", "confidence", "probabilities"),
    [
        (
            PAIR,
            REL_V1,
            {"partial_overlap": 0.53, "A_contains_B": 0.37, "same_area_distinct": 0.1},
            "rel",
            "partial_overlap",
            0.51,
            {
                "duplicate": 0.0,
                "A_contains_B": 0.37,
                "B_contains_A": 0.0,
                "partial_overlap": 0.53,
                "same_area_distinct": 0.1,
                "unrelated": 0.0,
            },
        ),
        (STORY, ATOMIC_V2, {"yes": 0.73}, "atomic", None, None, {"yes": 0.73}),
    ],
    ids=["pair-rel.v1", "story-atomic.v2"],
)
async def test_decide_sends_the_probe_request_and_parses_the_answer(
    state: str | Mapping[str, str],
    question: Question,
    probs: Mapping[str, float],
    key: str,
    choice: str | None,
    confidence: float | None,
    probabilities: Mapping[str, float],
) -> None:
    """DESIGN §2.7 and questions.py: POST alpha/decisions, bearer key, model, payload under key."""
    fake = FakeJev(lambda _state, _qid: probs, api_key=DUMMY_KEY, latency_s=0.02)

    async with aclosing(_client(fake, model="typesafe/jev-test")) as client:
        answer = await client.decide(state, question)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    # FakeJev names the question by payload hash, so question_id proves the payload was verbatim.
    assert (call.state, call.question_id, call.key, call.model, call.outcome) == (
        state,
        question.id,
        key,
        "typesafe/jev-test",
        "ok",
    )
    assert answer.question_id == question.id
    assert answer.choice == choice
    assert answer.confidence == confidence
    assert answer.probabilities == probabilities
    assert answer.model == RESOLVED_MODEL
    assert answer.input_tokens == input_tokens_for(state)
    assert answer.cost_usd == pytest.approx(cost_for(state))
    assert answer.gen_id == "gen-dec-1790000000-FAKE0000000000000001"
    assert answer.latency_ms >= 15.0


# --- status classification ------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "status", "retry_after_s"),
    [
        (http_error(429, retry_after=7), 429, 7.0),
        (http_error(429), 429, None),
        (http_error(408), 408, None),
        (http_error(500), 500, None),
        (http_error(502), 502, None),
        (http_error(503, retry_after=2), 503, 2.0),
        (http_error(402, in_flight=True), 402, None),
    ],
    ids=["429-retry-after", "429", "408", "500", "502", "503-retry-after", "402-in-flight"],
)
async def test_retryable_http_statuses(
    failure: Failure, status: int, retry_after_s: float | None
) -> None:
    """DESIGN §2.7: 429, 408, 5xx and an in-flight 402 are retried, honouring Retry-After."""
    fake = FakeJev()
    fake.fail(failure)

    async with aclosing(_client(fake)) as client:
        with pytest.raises(JevRetryable) as excinfo:
            await client.decide(PAIR, REL_V1)

    assert excinfo.value.status == status
    assert excinfo.value.retry_after_s == retry_after_s


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [transport_error(), timeout_error(), missing_cost(), missing_answers()],
    ids=["connect-error", "read-timeout", "200-without-cost", "200-without-answers"],
)
async def test_transport_errors_and_incomplete_200s_are_retryable(failure: Failure) -> None:
    """DESIGN §2.7: httpx.TransportError or a 200 lacking `usage.cost` or `answers` is retried."""
    fake = FakeJev()
    fake.fail(failure)

    async with aclosing(_client(fake)) as client:
        with pytest.raises(JevRetryable):
            await client.decide(STORY, ATOMIC_V2)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        http_error(400),
        http_error(401),
        http_error(402),
        http_error(403),
        http_error(404),
        http_error(422),
    ],
    ids=["400", "401", "402-credits", "403", "404", "422"],
)
async def test_other_client_errors_are_fatal_with_status(failure: Failure) -> None:
    """DESIGN §2.7 and §7 exit 5: 400, 401, credits 402 and other 4xx are fatal."""
    fake = FakeJev()
    fake.fail(failure)

    async with aclosing(_client(fake)) as client:
        with pytest.raises(JevFatal) as excinfo:
            await client.decide(PAIR, REL_V1)

    assert excinfo.value.status == failure.status


@pytest.mark.anyio
async def test_wrong_key_is_fatal_401() -> None:
    """DESIGN §2.7 and §7 exit 5 (Jev auth): the client sends `Authorization: Bearer <key>`."""
    fake = FakeJev(api_key="sk-or-v1-the-right-key")

    async with aclosing(_client(fake, api_key="sk-or-v1-a-wrong-key")) as client:
        with pytest.raises(JevFatal) as excinfo:
            await client.decide(PAIR, REL_V1)

    assert excinfo.value.status == 401


# --- key usage ------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_key_usage_usd_reads_data_usage_and_tracks_spend(jev_bodies: dict[str, Any]) -> None:
    """DESIGN §2.7 Cost: `GET /api/v1/key` usage before and after a run."""
    start = jev_bodies["key_usage"]["data"]["usage"]
    fake = FakeJev(api_key=DUMMY_KEY, key_usage_start=start)

    async with aclosing(_client(fake)) as client:
        before = await client.key_usage_usd()
        await client.decide(STORY, ATOMIC_V2)
        after = await client.key_usage_usd()

    assert before == pytest.approx(0.000789768)
    assert after == pytest.approx(0.000789768 + cost_for(STORY))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("key_status", "fake_key"),
    [(500, DUMMY_KEY), (200, "sk-or-v1-the-right-key")],
    ids=["500", "401-wrong-key"],
)
async def test_key_usage_usd_is_none_when_the_call_fails(key_status: int, fake_key: str) -> None:
    """DESIGN §8 key_usage_usd returns None on failure; the run carries on without it."""
    fake = FakeJev(api_key=fake_key, key_status=key_status)

    async with aclosing(_client(fake)) as client:
        assert await client.key_usage_usd() is None


# --- the key never leaks --------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "fake_key"),
    [
        (None, "sk-or-v1-the-right-key"),
        (http_error(401), DUMMY_KEY),
        (http_error(400), DUMMY_KEY),
        (http_error(503), DUMMY_KEY),
        (transport_error(), DUMMY_KEY),
        (missing_answers(), DUMMY_KEY),
    ],
    ids=["wrong-key", "401", "400", "503", "connect-error", "200-without-answers"],
)
async def test_api_key_never_appears_in_errors_or_logs(
    caplog: pytest.LogCaptureFixture, failure: Failure | None, fake_key: str
) -> None:
    """DESIGN §7: env values are never logged; JevError messages never contain the key."""
    caplog.set_level(logging.DEBUG)
    fake = FakeJev(api_key=fake_key)
    if failure is not None:
        fake.fail(failure)

    async with aclosing(_client(fake)) as client:
        with pytest.raises(JevError) as excinfo:
            await client.decide(PAIR, REL_V1)
        client_repr = repr(client)

    error = excinfo.value
    rendered = "".join(traceback.format_exception(error))
    for text in (str(error), repr(error), rendered, caplog.text, client_repr):
        assert DUMMY_KEY not in text
