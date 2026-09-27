"""Paths and expectations of the web UI fixture (tests/fixtures/ui/MANIFEST.md, binding).

`tests/fixtures/ui/build_fixture.py` writes the fixture; tests read their expected counts and keys
from its `manifest.json` instead of hard-coding them. The API is these names only:

- `UI_DIR`, `RUNS_ROOT`, `SOURCE_V1`, `DECISIONS_SEED`, `MANIFEST_PATH`, `BUILDER`: fixture paths.
- `NOT_BUILT`: the skip reason when the fixture is absent.
- `manifest()`: the parsed manifest.json; calls `pytest.skip(NOT_BUILT)` if it was never built.
- `run_dir(ref)`: the run directory of a ref, e.g. `mini~20260101-090000` -> RUNS_ROOT/mini/….

Call `manifest()` inside a test (not at import), so an unbuilt fixture skips instead of erroring.
"""

import json
from pathlib import Path
from typing import Any

import pytest

UI_DIR = Path(__file__).parent / "fixtures" / "ui"
RUNS_ROOT = UI_DIR / "runs"
SOURCE_V1 = UI_DIR / "source.v1.json"
DECISIONS_SEED = UI_DIR / "decisions.seed.json"
MANIFEST_PATH = UI_DIR / "manifest.json"
BUILDER = UI_DIR / "build_fixture.py"
NOT_BUILT = "fixture not built: run tests/fixtures/ui/build_fixture.py"


def manifest() -> dict[str, Any]:
    """The fixture's manifest.json; skips the calling test when the fixture is not built."""
    if not MANIFEST_PATH.is_file():
        pytest.skip(NOT_BUILT)
    data: dict[str, Any] = json.loads(MANIFEST_PATH.read_text("utf-8"))
    return data


def run_dir(ref: str) -> Path:
    """The run directory a ref names (UI DESIGN §4.2: `/` in the path is `~` in the ref)."""
    return RUNS_ROOT.joinpath(*ref.split("~"))
