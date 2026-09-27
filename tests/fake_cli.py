#!/usr/bin/env python3
"""A stand-in for the `tesselint` CLI that `tesselint serve` spawns (docs/ui/DESIGN.md §4.4, §6).

DESIGN §6 names it `tests/fakes/fake_cli.py`; it lives at `tests/fake_cli.py` because
`tests/fakes.py` (FakeJev) already owns the `tests.fakes` module name.

Point the server at it with `ServeConfig(cli=fake_cli_argv())`, `JobManager(cli=...)` or
`TESSELINT_UI_CLI=<this file>` (the file is executable). It imports only the standard library
and never touches the network.

Argv shapes (the ones JobManager and `/api/key` spawn):

    run [--source S] [--tier T | --epics E1,E2] [--resume R] --estimate-json
    run [--source S] [--tier T | --epics E1,E2] [--resume R] --max-usd X --out D --progress-jsonl P
    key --json

Environment:

    FAKE_CLI_MODE           ok (default) | cap | auth | slow | bad_input
    FAKE_CLI_LOG            a file; every invocation appends {"argv", "pid", "pgid", "sid", "mode"}
                            and every signal received appends {"signal", "pid"} (JSON lines)
    FAKE_CLI_USD            the estimate in US$ (default 0.05)
    FAKE_CLI_REMAINING      the key's remaining balance in US$ (default 1.12 of a 2.0 limit)
    FAKE_CLI_IGNORE_SIGINT  "1": `slow` ignores SIGINT, so only SIGTERM stops it

Modes of `run` (the estimate succeeds in every mode but `bad_input`):

    ok         run_dir, estimate and phase events, run.json `complete`, done; exit 0
    cap        stops half way: run.json `partial_cap`, a spend-cap error line; exit 3
    auth       no run dir; an error line that ECHOES OPENROUTER_API_KEY: a hostile CLI, so tests
               can check that the server redacts `sk-or-…` from log tails, messages and job.json;
               exit 5
    slow       a phase event about every 0.5 s for up to 60 s; SIGINT writes run.json
               `interrupted` and exits 130; SIGTERM exits 143
    bad_input  an unparsed-line error line on both estimate and run; exit 4

`key --json` prints a KeyStatus (present, usage_usd, limit_usd, remaining_usd, error) and never
the key; `present` follows OPENROUTER_API_KEY.

The helpers at the bottom (`read_log`, `spawn_violations`, `kill_leftovers`, `wait_for`) let tests
read the log; they are not used when the file runs as the CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType
from typing import Any, TypeVar

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CAP = 3
EXIT_BAD_INPUT = 4
EXIT_AUTH = 5
EXIT_INTERRUPTED = 130
EXIT_TERMINATED = 143

TO_ASK = 12
CACHED = 30
LIMIT_USD = 2.0
DEFAULT_CAP_USD = 1.0
PHASES = (("fwd", 12), ("atomic", 6), ("swap", 4))
SLOW_STEP_S = 0.05
SLOW_EVENT_EVERY = 10  # steps: one phase event about every 0.5 s
SLOW_MAX_STEPS = 1200  # 60 s
UNPARSED_LINE = "1 unparsed story line(s) in {source}; fix them first"

FAKE_CLI = Path(__file__).resolve()


def _mode() -> str:
    state = os.environ.get("TESSELINT_STATE_DIR")
    if state:
        mode_file = Path(state) / "fake_cli_mode"
        if mode_file.is_file():
            mode = mode_file.read_text("utf-8").strip()
            if mode:
                return mode
    return os.environ.get("FAKE_CLI_MODE", "ok")


def _log(record: dict[str, Any]) -> None:
    path = os.environ.get("FAKE_CLI_LOG")
    if path:
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


def _error(message: str) -> None:
    print(f"tesselint: error: {message}", file=sys.stderr, flush=True)


def _source_sha(args: argparse.Namespace) -> str:
    if args.resume is not None:
        sha_file = Path(args.resume) / "snapshot.sha256"
        if sha_file.is_file():
            return sha_file.read_text("utf-8").split()[0]
    if args.source is not None and Path(args.source).is_file():
        return hashlib.sha256(Path(args.source).read_bytes()).hexdigest()
    return hashlib.sha256(b"").hexdigest()


def _usd() -> float:
    return float(os.environ.get("FAKE_CLI_USD", "0.05"))


class _Events:
    """Appends §7.4 progress events to `--progress-jsonl`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")


def _new_run_dir(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while True:
        run_dir = out / (run_id if n == 1 else f"{run_id}-{n}")
        try:
            run_dir.mkdir()
        except FileExistsError:
            n += 1
            continue
        return run_dir


def _write_run_json(run_dir: Path, args: argparse.Namespace, status: str, spent: float) -> None:
    meta = {
        "run_id": run_dir.name,
        "started_at": "2026-09-26T12:00:00+00:00",
        "finished_at": None if status == "running" else "2026-09-26T12:00:05+00:00",
        "snapshot_sha256": _source_sha(args),
        "source": {"path": args.source, "format": "md"},
        "kind": "real",
        "dry_run": False,
        "tier": args.tier,
        "epics": args.epics.split(",") if args.epics else None,
        "n_stories": 8,
        "max_usd": args.max_usd,
        "status": status,
        "calls": {"live": TO_ASK, "cached": CACHED, "failed": 0, "retries": 0, "by_status": {}},
        "cost_usd": {"live": spent, "cached": 0.0, "preflight_estimate": _usd()},
        "wall_s": 5.0,
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n", "utf-8")
    (run_dir / "snapshot.sha256").write_text(f"{meta['snapshot_sha256']}  snapshot.md\n", "utf-8")


def _estimate(args: argparse.Namespace) -> int:
    if _mode() == "bad_input":
        _error(UNPARSED_LINE.format(source=args.source))
        return EXIT_BAD_INPUT
    usd = _usd()
    cap = args.max_usd if args.max_usd is not None else DEFAULT_CAP_USD
    line = {
        "to_ask": TO_ASK,
        "cached": CACHED,
        "usd": usd,
        "cap_usd": cap,
        "over_cap": usd > cap,
        "snapshot_sha256": _source_sha(args),
    }
    print(json.dumps(line), flush=True)
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    if args.max_usd is None or args.out is None or args.progress_jsonl is None:
        _error("fake_cli: a real run needs --max-usd, --out and --progress-jsonl")
        return EXIT_USAGE
    mode = _mode()
    if mode == "bad_input":
        _error(UNPARSED_LINE.format(source=args.source))
        return EXIT_BAD_INPUT
    if mode == "auth":
        key = os.environ.get("OPENROUTER_API_KEY", "")
        _error(f"Jev refused the request: HTTP 402 Insufficient credits (Bearer {key})")
        return EXIT_AUTH

    emit = _Events(Path(args.progress_jsonl))
    emit({"event": "estimate", "to_ask": TO_ASK, "usd": _usd()})
    run_dir = _new_run_dir(Path(args.out))
    _write_run_json(run_dir, args, "running", 0.0)
    emit({"event": "run_dir", "run_dir": str(run_dir)})
    print(f"run dir: {run_dir}", file=sys.stderr, flush=True)
    if mode == "slow":
        return _slow(args, emit, run_dir)

    spent = 0.0
    for name, total in PHASES:
        done = total // 2 if mode == "cap" else total
        if name == "fwd":
            spent = _usd() * done / total
        emit({"event": "phase", "phase": name, "done": done, "total": total, "spent_usd": spent})
        print(f"{name}: {done}/{total}", file=sys.stderr, flush=True)
        if mode == "cap":
            break
    status = "partial_cap" if mode == "cap" else "complete"
    _write_run_json(run_dir, args, status, spent)
    emit({"event": "done", "status": status})
    print(
        f"{status} · {TO_ASK} live / {CACHED} cached calls · ${spent:.4f} → {run_dir}", flush=True
    )
    if mode == "cap":
        _error(
            f"stopped at the spend cap of ${args.max_usd:g} (estimate ${_usd():.4f},"
            f" spent ${spent:.4f}); raise --max-usd or TESSELINT_MAX_USD"
        )
        return EXIT_CAP
    return EXIT_OK


def _slow(args: argparse.Namespace, emit: _Events, run_dir: Path) -> int:
    def on_term(signum: int, frame: FrameType | None) -> None:
        _log({"signal": "SIGTERM", "pid": os.getpid()})
        os._exit(EXIT_TERMINATED)

    def on_int(signum: int, frame: FrameType | None) -> None:
        _log({"signal": "SIGINT", "pid": os.getpid()})
        if os.environ.get("FAKE_CLI_IGNORE_SIGINT") != "1":
            raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_int)
    total = SLOW_MAX_STEPS // SLOW_EVENT_EVERY
    try:
        for step in range(SLOW_MAX_STEPS):
            if step % SLOW_EVENT_EVERY == 0:
                done = step // SLOW_EVENT_EVERY
                spent = _usd() * done / total
                emit(
                    {
                        "event": "phase",
                        "phase": "fwd",
                        "done": done,
                        "total": total,
                        "spent_usd": spent,
                    }
                )
            time.sleep(SLOW_STEP_S)
    except KeyboardInterrupt:
        _write_run_json(run_dir, args, "interrupted", 0.0)
        emit({"event": "done", "status": "interrupted"})
        _error(f"interrupted; resume with: tesselint run --resume {run_dir}")
        return EXIT_INTERRUPTED
    _write_run_json(run_dir, args, "complete", _usd())
    emit({"event": "done", "status": "complete"})
    return EXIT_OK


def _key() -> int:
    present = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    status: dict[str, Any]
    if present:
        remaining = float(os.environ.get("FAKE_CLI_REMAINING", "1.12"))
        status = {
            "present": True,
            "usage_usd": round(LIMIT_USD - remaining, 6),
            "limit_usd": LIMIT_USD,
            "remaining_usd": remaining,
            "error": None,
        }
    else:
        status = {
            "present": False,
            "usage_usd": None,
            "limit_usd": None,
            "remaining_usd": None,
            "error": "OPENROUTER_API_KEY is not set",
        }
    print(json.dumps(status), flush=True)
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tesselint")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--source")
    scope = run.add_mutually_exclusive_group()
    scope.add_argument("--tier")
    scope.add_argument("--epics")
    run.add_argument("--resume")
    run.add_argument("--estimate-json", action="store_true")
    run.add_argument("--max-usd", type=float)
    run.add_argument("--out")
    run.add_argument("--progress-jsonl")
    key = sub.add_parser("key")
    key.add_argument("--json", action="store_true", required=True)
    return parser


def main(argv: list[str]) -> int:
    _log(
        {
            "argv": argv,
            "pid": os.getpid(),
            "pgid": os.getpgid(0),
            "sid": os.getsid(0),
            "mode": _mode(),
        }
    )
    args = _parser().parse_args(argv)
    if args.command == "key":
        return _key()
    return _estimate(args) if args.estimate_json else _run(args)


# --- helpers for tests ----------------------------------------------------------------------------

JOB_FLAGS = ("--max-usd", "--out", "--progress-jsonl")  # flags with a value that only a job adds
T = TypeVar("T")


def fake_cli_argv() -> tuple[str, ...]:
    """The `cli` argv prefix that runs this fake with the current interpreter."""
    return (sys.executable, str(FAKE_CLI))


def read_log(path: Path) -> list[dict[str, Any]]:
    """Every record the fake appended to FAKE_CLI_LOG, in order (empty if none)."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def invocations(path: Path) -> list[dict[str, Any]]:
    """The invocation records (argv, pid, pgid, sid, mode), without signal records."""
    return [record for record in read_log(path) if "argv" in record]


def is_estimate(argv: Sequence[str]) -> bool:
    return argv[:1] == ["run"] and "--estimate-json" in argv


def is_job_run(argv: Sequence[str]) -> bool:
    """A spending `run`: a `run` without `--estimate-json`."""
    return argv[:1] == ["run"] and "--estimate-json" not in argv


def flag_value(argv: Sequence[str], flag: str) -> str | None:
    """The value after `flag` (or of `flag=value`), else None."""
    for i, word in enumerate(argv):
        if word == flag and i + 1 < len(argv):
            return argv[i + 1]
        if word.startswith(flag + "="):
            return word.split("=", 1)[1]
    return None


def scope_argv(argv: Sequence[str]) -> list[str]:
    """`argv` without `--estimate-json` and the job-only flags and their values."""
    out: list[str] = []
    skip = False
    for word in argv:
        if skip:
            skip = False
        elif word == "--estimate-json":
            continue
        elif word in JOB_FLAGS:
            skip = True
        elif not any(word.startswith(flag + "=") for flag in JOB_FLAGS):
            out.append(word)
    return out


def spawn_violations(path: Path) -> list[str]:
    """Spending runs that break §4.4: no `--max-usd`, or no earlier estimate of the same scope."""
    estimated: list[list[str]] = []
    problems: list[str] = []
    for record in invocations(path):
        argv: list[str] = record["argv"]
        if is_estimate(argv):
            estimated.append(scope_argv(argv))
        elif is_job_run(argv):
            if flag_value(argv, "--max-usd") is None:
                problems.append(f"run spawned without --max-usd: {argv}")
            if scope_argv(argv) not in estimated:
                problems.append(f"run spawned without an estimate of the same argv: {argv}")
    return problems


def kill_leftovers(path: Path) -> None:
    """SIGKILL every spending fake run still alive (its process group only if it leads one)."""
    for record in invocations(path):
        if not is_job_run(record["argv"]):
            continue  # estimates and `key` are short and awaited by the server
        pid, pgid = int(record["pid"]), int(record["pgid"])
        try:
            if pgid == pid:
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


# A TypeVar, not PEP 695 syntax: the fake may run under an older `python3` via its shebang.
def wait_for(fetch: Callable[[], T], done: Callable[[T], bool], timeout: float = 20.0) -> T:  # noqa: UP047
    """Call `fetch` every 0.1 s until `done(value)`; on timeout, fail with the last value."""
    deadline = time.monotonic() + timeout
    while True:
        value = fetch()
        if done(value):
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout:g} s; last value: {value!r}")
        time.sleep(0.1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
