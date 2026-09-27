"""Two real Jev calls through OpenRouter (DESIGN §2.1 questions, §2.7 cost). Costs < US$0.002.

Deselected by default (`-m 'not live'` in pyproject); run with `uv run pytest -m live`. Skipped
unless OPENROUTER_API_KEY is set in the environment.
"""

import os
from contextlib import aclosing

import pytest

from tesselint.config import DEFAULT_BASE_URL, DEFAULT_MODEL
from tesselint.jev import JevClient
from tesselint.models import Relation
from tesselint.parse import parse_section
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.render import render, text_hash
from tests.conftest import require_section4

pytestmark = [
    pytest.mark.live,
    pytest.mark.anyio,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY is not set"
    ),
]

MAX_COST_USD = 0.001


def _rendered(story_id: str) -> str:
    stories = parse_section(require_section4().read_text("utf-8")).stories
    return render(next(s for s in stories if s.id == story_id))


def _client() -> JevClient:
    return JevClient(
        os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        model=os.environ.get("TESSELINT_MODEL", DEFAULT_MODEL),
    )


async def test_live_rel_v1_on_us_1_1_and_us_20_2() -> None:
    """DESIGN §2.1 rel.v1 on the known pair, A = the text with the smaller sha256."""
    a, b = sorted((_rendered("US-1.1"), _rendered("US-20.2")), key=text_hash)

    async with aclosing(_client()) as client:
        answer = await client.decide({"A": a, "B": b}, REL_V1)

    options = {r.value for r in Relation}
    assert answer.choice in options
    assert set(answer.probabilities) <= options
    assert sum(answer.probabilities.values()) == pytest.approx(1.0, abs=0.03)
    assert 0 < answer.cost_usd < MAX_COST_USD


async def test_live_atomic_v2_on_us_1_1() -> None:
    """DESIGN §2.1 atomic.v2: a noul answer `{"yes": p}` with p in [0, 1]."""
    async with aclosing(_client()) as client:
        answer = await client.decide(_rendered("US-1.1"), ATOMIC_V2)

    assert answer.choice is None
    assert set(answer.probabilities) == {"yes"}
    assert 0.0 <= answer.probabilities["yes"] <= 1.0
    assert 0 < answer.cost_usd < MAX_COST_USD
