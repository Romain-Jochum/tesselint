"""Shared fixtures.

Every test not marked `live` runs with a dummy OPENROUTER_API_KEY, an unroutable
OPENROUTER_BASE_URL, TESSELINT_SOURCE at the public synthetic backlog and a per-test
TESSELINT_CACHE_DIR, so no test can spend money or read a live source.

The §4 fixture (a real backlog, 176 stories) is private: it lives in the gitignored `private/`
folder. Tests that need it call `require_section4()` or take the `real_section4` fixture, and skip
when it is absent; `tests/test_sample_backlog.py` covers the parser on a public synthetic file.

python-dotenv does not override variables that are already set; a test that deletes
OPENROUTER_API_KEY must also keep the real .env out of reach.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeJev

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parent.parent
SAMPLE_BACKLOG = FIXTURES / "sample_backlog.md"
SECTION4 = REPO / "private" / "fixtures" / "section4_2026-09-26.md"


def require_section4() -> Path:
    """The private §4 fixture, or skip the calling test when it is absent (public checkouts)."""
    if not SECTION4.is_file():
        pytest.skip(f"private fixture absent: {SECTION4.relative_to(REPO)}")
    return SECTION4


@pytest.fixture
def anyio_backend() -> str:
    """Run `@pytest.mark.anyio` tests on asyncio only (the app is asyncio)."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _offline_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live") is not None:
        return
    tmp_path: Path = request.getfixturevalue("tmp_path")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-not-a-real-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://jev.invalid/api")
    monkeypatch.setenv("TESSELINT_SOURCE", str(SAMPLE_BACKLOG))
    monkeypatch.setenv("TESSELINT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("TESSELINT_MODEL", raising=False)
    monkeypatch.delenv("TESSELINT_MAX_USD", raising=False)


@pytest.fixture
def real_section4() -> Path:
    """Path to the frozen private §4 snapshot (176 stories, 3 tombstones); skips when absent."""
    return require_section4()


@pytest.fixture
def jev_bodies() -> dict[str, Any]:
    """Real Jev response bodies by name (see fixtures/README.md); a fresh copy per test."""
    bodies: dict[str, Any] = json.loads((FIXTURES / "jev_bodies.json").read_text("utf-8"))
    return bodies


@pytest.fixture
def fake_jev() -> type[FakeJev]:
    """Factory for FakeJev: `fake = fake_jev(answer_fn, latency_s=0.01)`."""
    return FakeJev


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """An empty cache directory; TESSELINT_CACHE_DIR already points at it."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir
