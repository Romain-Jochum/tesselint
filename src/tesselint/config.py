"""Settings, tiers and environment defaults (DESIGN §7, §8 config.py)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from tesselint.models import Thresholds

DEFAULT_BASE_URL = "https://openrouter.ai/api"
DEFAULT_MODEL = "typesafe/jev-1.13"
USD_PER_TOKEN = 4.2e-8

# Defaults of the env vars in DESIGN §7 (OPENROUTER_BASE_URL and TESSELINT_MODEL use the two above).
# TESSELINT_SOURCE has none: `parse` and `run` need --source or the variable.
DEFAULT_CACHE_DIR = Path(".cache/tesselint")
DEFAULT_MAX_USD = 1.0
DEFAULT_OUT_ROOT = Path("runs")
DEFAULT_STATE_DIR = Path(".tesselint")  # web UI decisions, jobs, exports (docs/ui/DESIGN.md §3)

# `tesselint serve` defaults (docs/ui/DESIGN.md §4.1).
DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8765
DEFAULT_UI_MAX_USD = 1.0

# The DESIGN §7 variables that have a default (OPENROUTER_API_KEY and TESSELINT_SOURCE have none).
ENV_DEFAULTS: Mapping[str, str] = MappingProxyType(
    {
        "OPENROUTER_BASE_URL": DEFAULT_BASE_URL,
        "TESSELINT_MODEL": DEFAULT_MODEL,
        "TESSELINT_CACHE_DIR": str(DEFAULT_CACHE_DIR),
        "TESSELINT_MAX_USD": str(DEFAULT_MAX_USD),
        "TESSELINT_STATE_DIR": str(DEFAULT_STATE_DIR),
    }
)

# The CLI's operating point (docs/results.md "Calibration"): on 160 blind-labelled pairs none below
# o 0.5 was related and 9 of 10 related pairs had o >= 0.7, so clusters (overlap proposals) cut at
# 0.7. Merge and split keep the DESIGN §2.5 values; models.Thresholds() stays the DESIGN default.
CALIBRATED_THRESHOLDS = Thresholds(cluster=0.7)

TIERS: Mapping[str, tuple[str, ...] | None] = MappingProxyType(
    {
        "t1": ("E1", "E2", "E20"),
        "t2": ("E1", "E2", "E20", "E3", "E4", "E23"),
        "t3": None,
    }
)


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Everything `tesselint run` needs, resolved from CLI flags and env (DESIGN §7)."""

    source: Path
    out_root: Path
    epics: tuple[str, ...] | None
    render: Literal["full", "core"]
    prefilter: bool
    prefilter_k: int
    swap: bool
    concurrency: int
    rate_per_s: float
    max_usd: float
    cache_dir: Path
    model: str
    base_url: str
    resume: Path | None
    refresh: bool
    dry_run: bool
    thresholds: Thresholds
    tier: str | None = None


NO_SOURCE = "no source: pass --source or set TESSELINT_SOURCE"


def env(name: str) -> str:
    """The DESIGN §7 variable `name` from the environment, or its default when unset or empty."""
    return os.environ.get(name) or ENV_DEFAULTS[name]


def env_source() -> Path | None:
    """TESSELINT_SOURCE as a path, or None when it is unset or empty (there is no default)."""
    value = os.environ.get("TESSELINT_SOURCE")
    return Path(value) if value else None
