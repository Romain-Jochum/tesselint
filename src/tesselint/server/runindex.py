"""Run index: refs, kind and dry-run inference over the runs root (docs/ui/DESIGN.md §4.2)."""

import json
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

MAX_DEPTH = 4
REF_SEP = "~"  # a ref is the run dir under the runs root with `/` → `~`
REF_PATTERN = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(~[A-Za-z0-9_-][A-Za-z0-9._-]*){0,3}$")
SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")  # one directory name a ref can hold
SYNTH_SEGMENT = re.compile(r"^synth(?:$|[-_.])")  # legacy synthetic runs: synth/, synth-v2/, …
RUN_FILE = "run.json"
RUN_ID_TIME = "%Y%m%d-%H%M%S"
EPOCH = datetime.min.replace(tzinfo=UTC)

Kind = Literal["real", "synthetic"]


@dataclass(frozen=True, slots=True)
class RunEntry:
    """One run directory found by `scan_runs`; `meta` is its run.json (§4.2, §7.1)."""

    ref: str
    path: Path
    meta: Mapping[str, Any]
    kind: Literal["real", "synthetic"]
    kind_inferred: bool
    dry_run: bool


def scan_runs(runs_root: Path, max_depth: int = MAX_DEPTH) -> list[RunEntry]:
    """Every run under `runs_root` (os.walk, followlinks=False) with its ref and kind (§4.2).

    A run is a directory holding a regular run.json object, at most `max_depth` levels down,
    whose path segments can form a ref. Symlinked directories and run.json files are skipped.
    """
    if not runs_root.is_dir():
        return []
    entries: list[RunEntry] = []
    for folder, dirnames, filenames in os.walk(runs_root, followlinks=False):
        path = Path(folder)
        parts = path.relative_to(runs_root).parts
        keep = len(parts) < max_depth
        dirnames[:] = sorted(d for d in dirnames if keep and SEGMENT.fullmatch(d))
        if parts and RUN_FILE in filenames:
            entry = _entry(path, parts)
            if entry is not None:
                entries.append(entry)
    return entries


def _entry(path: Path, parts: Sequence[str]) -> RunEntry | None:
    ref = REF_SEP.join(parts)
    run_json = path / RUN_FILE
    if not REF_PATTERN.fullmatch(ref) or run_json.is_symlink() or not run_json.is_file():
        return None
    try:
        meta = json.loads(run_json.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None  # unreadable, or being written: not a run the API can show yet
    if not isinstance(meta, dict):
        return None
    kind, inferred = _kind(meta, parts)
    return RunEntry(ref, path, meta, kind, inferred, _dry_run(meta))


def _kind(meta: Mapping[str, Any], parts: Sequence[str]) -> tuple[Kind, bool]:
    """run.json `kind`, else synthetic when a ref segment is `synth` (or `synth-…`) (§4.2)."""
    kind = meta.get("kind")
    if kind == "real" or kind == "synthetic":
        return kind, False
    synthetic = any(SYNTH_SEGMENT.match(part) for part in parts)
    return ("synthetic" if synthetic else "real"), True


def _dry_run(meta: Mapping[str, Any]) -> bool:
    """run.json `dry_run`, else dry when calls.live + calls.cached == 0 and jobs.fwd > 0 (§4.2)."""
    dry = meta.get("dry_run")
    if isinstance(dry, bool):
        return dry
    calls, jobs = meta.get("calls"), meta.get("jobs")
    if not isinstance(calls, Mapping) or not isinstance(jobs, Mapping):
        return False
    answered = number(calls.get("live")) + number(calls.get("cached"))
    return answered == 0 and number(jobs.get("fwd")) > 0


def number(value: Any) -> float:
    """A finite JSON number as a float; anything else is 0."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value) if math.isfinite(value) else 0.0


def started(entry: RunEntry) -> datetime:
    """When a run started: run.json `started_at`, else its run ID's time, else the epoch."""
    raw = entry.meta.get("started_at")
    if isinstance(raw, str):
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            pass
        else:
            return when if when.tzinfo else when.replace(tzinfo=UTC)
    run_id = entry.meta.get("run_id")
    if isinstance(run_id, str):
        try:
            return datetime.strptime(run_id[:15], RUN_ID_TIME).replace(tzinfo=UTC)
        except ValueError:
            pass
    return EPOCH


def newest_first(entries: Iterable[RunEntry]) -> list[RunEntry]:
    """Entries by start time, newest first; ties by ref, descending."""
    return sorted(entries, key=lambda e: (started(e), e.ref), reverse=True)


def latest(entries: Iterable[RunEntry]) -> RunEntry | None:
    """The newest non-dry `complete` real run, else of any kind (§4.2, PLAN resolution 4)."""
    done = [
        e for e in newest_first(entries) if not e.dry_run and e.meta.get("status") == "complete"
    ]
    real = [e for e in done if e.kind == "real"]
    return next(iter(real or done), None)


def previous(entries: Iterable[RunEntry], entry: RunEntry) -> RunEntry | None:
    """The newest earlier non-dry run of the same kind and tier (§4.2 Previous)."""
    when, tier = started(entry), entry.meta.get("tier")
    for other in newest_first(entries):
        if (
            other.ref != entry.ref
            and not other.dry_run
            and other.kind == entry.kind
            and other.meta.get("tier") == tier
            and started(other) < when
        ):
            return other
    return None
