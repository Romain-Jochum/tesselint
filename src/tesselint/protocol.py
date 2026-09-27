"""JSON the CLI prints for the web server to read (docs/ui/DESIGN.md §4.4, §7.3, §7.4).

Stdlib only: both `tesselint.cli` and `tesselint.server` import it, and the server must never
load `jev`, `httpx` or `dotenv` (§1).
"""

from typing import Literal, TypedDict


class KeyStatus(TypedDict):
    """`tesselint key --json` and `GET /api/key`; never holds the key itself (§7.3)."""

    present: bool
    usage_usd: float | None
    limit_usd: float | None
    remaining_usd: float | None
    error: str | None


class EstimateLine(TypedDict):
    """The one line `tesselint run --estimate-json` prints (§4.4)."""

    to_ask: int
    cached: int
    usd: float
    cap_usd: float
    over_cap: bool
    snapshot_sha256: str


# `run --progress-jsonl PATH` appends one JSON object per line; `event` names its kind (§7.4).


class RunDirEvent(TypedDict):
    """The run directory, once it exists."""

    event: Literal["run_dir"]
    run_dir: str


class EstimateEvent(TypedDict):
    """The preflight estimate."""

    event: Literal["estimate"]
    to_ask: int
    usd: float


class PhaseEvent(TypedDict):
    """Progress of one phase (fwd, atomic, swap), at most one per second."""

    event: Literal["phase"]
    phase: str
    done: int
    total: int
    spent_usd: float


class DoneEvent(TypedDict):
    """The run ended with this run.json `status`."""

    event: Literal["done"]
    status: str


ProgressEvent = RunDirEvent | EstimateEvent | PhaseEvent | DoneEvent
