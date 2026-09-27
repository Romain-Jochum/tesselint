"""HTTP client for Jev's decisions endpoint (DESIGN §2.7, §8 jev.py). Never logs the key."""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from tesselint.config import DEFAULT_BASE_URL, DEFAULT_MODEL
from tesselint.jobtypes import Answer as Answer  # re-exported: the type moved to jobtypes
from tesselint.questions import Question


@dataclass(frozen=True, slots=True)
class KeyInfo:
    """Balance of the key from GET /v1/key, in US$; `limit_usd` None means no limit (UI §7.1)."""

    usage_usd: float
    limit_usd: float | None


class JevError(Exception):
    """Base class for a failed Jev call. Messages never contain the key."""


class JevRetryable(JevError):
    """A failure worth retrying: 429, 408, 5xx, in-flight 402, transport error, bad 200.

    `cost_usd` is the `usage.cost` of a rejected 200 (it was billed); `maybe_billed` marks an
    attempt that reached Jev but reported no cost, such as a read timeout (DESIGN §2.7 Cost).
    """

    retry_after_s: float | None
    status: int | None
    cost_usd: float | None
    maybe_billed: bool

    def __init__(
        self,
        message: str,
        *,
        retry_after_s: float | None = None,
        status: int | None = None,
        cost_usd: float | None = None,
        maybe_billed: bool = False,
    ) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.status = status
        self.cost_usd = cost_usd
        self.maybe_billed = maybe_billed


class JevFatal(JevError):
    """A failure not worth retrying: 400, 401, credits 402 and other 4xx (exit 5)."""

    status: int

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


class JevClient:
    """Async Jev client over one httpx.AsyncClient; tests pass an httpx.MockTransport."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
        max_connections: int = 16,
    ) -> None:
        """Build the AsyncClient with bearer auth and a connection limit (DESIGN §2.7)."""
        self._api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        limits = httpx.Limits(
            max_connections=max_connections, max_keepalive_connections=max_connections
        )
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout_s,
            limits=limits,
        )

    def __repr__(self) -> str:
        return f"JevClient(model={self.model!r}, base_url={self.base_url!r})"

    async def decide(self, state: str | Mapping[str, str], question: Question) -> Answer:
        """Make one POST /alpha/decisions attempt and parse it, else raise JevError (§2.7)."""
        key = question.id.split(".")[0]
        payload = {
            "model": self.model,
            "state": state,
            "questions": {key: question.payload},
        }
        url = f"{self.base_url}/alpha/decisions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        start = time.perf_counter()
        try:
            response = await self._client.post(url, json=payload, headers=headers)
        except httpx.RequestError as e:
            # TransportError, DecodingError, TooManyRedirects. A read timeout or an undecodable
            # body means Jev got the request and may have billed it.
            billed = isinstance(e, httpx.ReadTimeout | httpx.DecodingError)
            raise JevRetryable(f"Request error: {type(e).__name__}", maybe_billed=billed) from None

        latency_ms = (time.perf_counter() - start) * 1000.0
        status = response.status_code
        retry_after_hdr = response.headers.get("Retry-After")
        retry_after_s: float | None = None
        if retry_after_hdr is not None:
            try:
                retry_after_s = float(retry_after_hdr)
            except ValueError:
                retry_after_s = None

        if status == 200:
            try:
                body = response.json()
            except ValueError as e:
                raise JevRetryable(
                    f"Invalid JSON response: {type(e).__name__}",
                    status=status,
                    retry_after_s=retry_after_s,
                    maybe_billed=True,
                ) from None
            if not isinstance(body, Mapping):
                raise JevRetryable(
                    "Response body is not a JSON object", status=200, maybe_billed=True
                )
            return parse_response(body, question, latency_ms)

        is_in_flight = False
        if status == 402:
            try:
                err_data = response.json().get("error", {})
                metadata = err_data.get("metadata", {})
                if (
                    metadata.get("limit_source") == "openrouter_in_flight_budget"
                    or "in flight" in err_data.get("message", "").lower()
                ):
                    is_in_flight = True
            except Exception:
                pass

        if status in (429, 408) or status >= 500 or (status == 402 and is_in_flight):
            raise JevRetryable(
                f"Jev retryable error (status {status})",
                status=status,
                retry_after_s=retry_after_s,
            )

        raise JevFatal(f"Jev fatal error (status {status})", status=status)

    async def key_usage_usd(self) -> float | None:
        """Return `data.usage` from GET /v1/key, or None if the call fails (DESIGN §2.7)."""
        url = f"{self.base_url}/v1/key"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = await self._client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                usage = data.get("data", {}).get("usage")
                if usage is not None:
                    return float(usage)
        except Exception:
            return None
        return None

    async def key_info(self) -> KeyInfo | None:
        """Usage and limit from the free GET /v1/key, or None if it fails (UI DESIGN §4.4).

        None covers a rejected key (401), a server error, a transport error and a body without
        a numeric `data.usage`; a `data.limit` of null means the key has no limit.
        """
        url = f"{self.base_url}/v1/key"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = await self._client.get(url, headers=headers)
            body = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return None
        data = body.get("data") if isinstance(body, Mapping) else None
        if not isinstance(data, Mapping):
            return None
        usage, raw_limit = _number(data.get("usage")), data.get("limit")
        limit = _number(raw_limit)
        if usage is None or (raw_limit is not None and limit is None):
            return None
        return KeyInfo(usage_usd=usage, limit_usd=limit)

    async def aclose(self) -> None:
        """Close the underlying AsyncClient."""
        await self._client.aclose()


def _number(value: Any) -> float | None:
    """`value` as a float if it is a real JSON number (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_response(body: Mapping[str, Any], question: Question, latency_ms: float) -> Answer:
    """Turn a 200 body into an Answer, or raise JevRetryable if any part is missing or malformed.

    A rejected body that still reports `usage.cost` was billed, so the error carries that cost.
    """
    usage = body.get("usage")
    cost_usd = _number(usage.get("cost")) if isinstance(usage, Mapping) else None

    def reject(reason: str) -> JevRetryable:
        return JevRetryable(f"Malformed 200: {reason}", status=200, cost_usd=cost_usd)

    if "answers" not in body:
        raise reject("missing 'answers'")
    if cost_usd is None or not isinstance(usage, Mapping):
        raise reject("missing or non-numeric 'usage.cost'")
    input_tokens = _number(usage.get("input_tokens", 0))
    if input_tokens is None:
        raise reject("non-numeric 'usage.input_tokens'")

    answers = body["answers"]
    if not isinstance(answers, Mapping):
        raise reject("'answers' is not an object")
    key = question.id.split(".")[0]
    if key not in answers:
        if len(answers) != 1:
            raise reject(f"no answer under {key!r}")
        key = next(iter(answers))  # a single answer under another key is still this question's
    ans_data = answers[key]
    if not isinstance(ans_data, Mapping):
        raise reject(f"answer {key!r} is not an object")

    choice: str | None = None
    confidence: float | None = None
    if question.payload.get("type") == "noul":
        yes = _number(ans_data.get("noul"))
        if yes is None:
            raise reject("noul answer without a numeric 'noul'")
        probabilities = {"yes": yes}
    else:
        raw_probs = ans_data.get("probabilities")
        if not isinstance(raw_probs, Mapping) or not raw_probs:
            raise reject("choice answer without probabilities")
        probabilities = {}
        for option, value in raw_probs.items():
            p = _number(value)
            if p is None:
                raise reject(f"non-numeric probability for {option!r}")
            probabilities[str(option)] = p
        raw_choice = ans_data.get("choice")
        choice = raw_choice if isinstance(raw_choice, str) else None
        confidence = _number(ans_data.get("confidence"))

    return Answer(
        question_id=question.id,
        choice=choice,
        confidence=confidence,
        probabilities=probabilities,
        model=str(body.get("model", "")),
        input_tokens=int(input_tokens),
        cost_usd=cost_usd,
        gen_id=str(body.get("id", "")),
        latency_ms=latency_ms,
    )
