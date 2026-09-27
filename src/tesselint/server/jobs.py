"""Estimate, start, poll and cancel `tesselint run` subprocesses (docs/ui/DESIGN.md §4.4).

Spending is guarded by one path: a run is spawned only from a live estimate of the same argv
(`--estimate-json`, kept ESTIMATE_TTL_S with the source's hash), always with `--max-usd`, only
while no other job runs, in a new session so that cancel can signal its whole process group.

Each job lives in `<state>/jobs/<id>/`: `job.json` (a Job plus pid and argv, §7.4), the CLI's
`progress.jsonl`, its combined stdout and stderr in `cli.log` (0600, redacted once the job ends)
and a `cancel` marker. Everything a Job shows is redacted of `sk-or-…`. A restarted server
re-attaches to a job by its pid; a dead job it did not spawn is resolved from its `run.json`.

Module constants are read at call time, so tests can shorten them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

from tesselint.config import TIERS
from tesselint.protocol import KeyStatus
from tesselint.server.decisions import pid_alive
from tesselint.server.errors import ApiError, ErrorCode
from tesselint.server.guard import SECRET_PATTERN
from tesselint.server.payloads import Estimate, Job, JobFile, JobPhase
from tesselint.server.runindex import REF_PATTERN, REF_SEP

ESTIMATE_TIMEOUT_S = 120.0
ESTIMATE_TTL_S = 15 * 60.0  # an estimate's argv and source hash are kept this long
POLL_INTERVAL_S = 1.0  # refreshed each second while running
CANCEL_GRACE_S = 20.0  # SIGTERM follows SIGINT after 20 s (§4.4 Cancel)
UI_RUNS_DIR = "ui"  # runs spawned by the UI land under `<runs>/ui/`
JOBS_DIR = "jobs"  # under <state>/ (§3 Location)

EXIT_MESSAGES: Mapping[int, str] = MappingProxyType(
    {
        3: "Stopped at the cap; resume pays only missing answers",
        5: "Jev refused the key or credits",
        6: "Too many failed calls",
        130: "Cancelled; partial outputs kept",
        1: "Unexpected; run it with --debug",
    }
)

KEY_TIMEOUT_S = 30.0  # `key --json` makes one free request to OpenRouter
DEFAULT_CLI: tuple[str, ...] = (sys.executable, "-m", "tesselint")
STATE_DIR_ENV = "TESSELINT_STATE_DIR"  # set for every CLI child: the fake CLI reads its mode file
JOB_FILE, PROGRESS_FILE, LOG_FILE, CANCEL_FILE = "job.json", "progress.jsonl", "cli.log", "cancel"
LOG_TAIL_LINES = 40
LOG_TAIL_BYTES = 64 * 1024
REDACTED = "[redacted]"
ERROR_PREFIX = "tesselint: "  # the CLI's error lines are `tesselint: error: …`
# ETA: about 50 calls/s at the default --rate (docs/results.md: 1,885 calls in 38 s), plus start-up.
ETA_CALLS_PER_S = 50.0
ETA_START_S = 5.0
CAP_EPSILON = 1e-9  # float noise allowed on both cap bounds
EXIT_OK, EXIT_BAD_INPUT, EXIT_INTERRUPTED = 0, 4, 130
# run.json `status` of a finished run: how a re-attached dead job ended (no exit code is known).
RUN_STATUS_STATE: Mapping[str, tuple[Literal["done", "failed", "cancelled"], str | None]] = (
    MappingProxyType(
        {
            "complete": ("done", None),
            "partial_cap": ("failed", EXIT_MESSAGES[3]),
            "partial_errors": ("failed", EXIT_MESSAGES[6]),
            "interrupted": ("cancelled", EXIT_MESSAGES[130]),
        }
    )
)
LOST_MESSAGE = "The server lost this job: its process ended without a finished run.json"
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
EPIC_ID = re.compile(r"^E[0-9]{1,3}$")
MISSING_SOURCE = "missing"  # the source hash of a file that does not exist

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunScope:
    """The optional scope filters of `tesselint run` (§4.4 Re-run)."""

    tier: str | None = None
    epics: tuple[str, ...] | None = None
    resume: str | None = None  # a run ref, resolved through the index


@dataclass(frozen=True, slots=True)
class _Pending:
    """A live estimate: what `start` may spawn, and the source hash it was made on."""

    run_args: tuple[str, ...]  # `run`, `--source S`, the scope flags; no `--estimate-json`
    source: Path
    source_sha256: str
    usd: float
    created: float  # time.monotonic()


def cli_prefix(command: str | None) -> tuple[str, ...]:
    """The CLI argv prefix: `TESSELINT_UI_CLI` split by shlex, else DEFAULT_CLI (resolution 5)."""
    words = shlex.split(command) if command else []
    return tuple(words) if words else DEFAULT_CLI


class JobManager:
    """One job at a time; the browser never sends an argv or a path (§4.4).

    Methods are thread-safe. `estimate` and `key_status` block on a subprocess (up to
    ESTIMATE_TIMEOUT_S and KEY_TIMEOUT_S): call them off the event loop.
    """

    def __init__(
        self, state_dir: Path, cli: Sequence[str], runs_root: Path, ui_max_usd: float
    ) -> None:
        """Keep jobs under `<state_dir>/jobs`; spawn `cli` (§4.4, §7.1)."""
        if not cli:
            raise ValueError("JobManager needs a CLI argv prefix")
        self.state_dir = state_dir
        self.jobs_dir = state_dir / JOBS_DIR
        self.cli = tuple(cli)
        self.runs_root = runs_root
        self.ui_max_usd = ui_max_usd
        self._lock = threading.RLock()
        self._estimates: dict[str, _Pending] = {}
        self._procs: dict[str, subprocess.Popen[bytes]] = {}

    # --- estimate and key -------------------------------------------------------------------------

    def estimate(self, source: Path, scope: RunScope) -> Estimate:
        """Spawn `run --estimate-json` (120 s timeout); keep argv and source hash 15 min (§4.4).

        A bad scope is a ValueError and an unknown resume ref 404 `run_not_found`; a failing or
        unreadable CLI is 502 `cli_failed` with the CLI's error line. The key status comes from
        `key --json`; `max_usd_allowed` is min(ui_max_usd, remaining).
        """
        run_args = ("run", "--source", str(source), *self._scope_args(scope))
        source_sha256 = _source_digest(source)
        proc = self._cli_run([*run_args, "--estimate-json"], ESTIMATE_TIMEOUT_S)
        if proc.returncode != 0:
            raise ApiError(ErrorCode.CLI_FAILED, _failure(proc, "run --estimate-json"))
        try:
            to_ask, cached, usd = _parse_estimate(proc.stdout)
        except ValueError as e:
            raise ApiError(
                ErrorCode.CLI_FAILED, f"unreadable output of run --estimate-json: {_redact(str(e))}"
            ) from None
        key = self.key_status()
        estimate_id = "est-" + secrets.token_hex(8)
        pending = _Pending(run_args, source, source_sha256, usd, time.monotonic())
        with self._lock:
            self._forget_expired()
            self._estimates[estimate_id] = pending
        expires = datetime.now(UTC) + timedelta(seconds=ESTIMATE_TTL_S)
        return Estimate(
            id=estimate_id,
            to_ask=to_ask,
            cached=cached,
            usd=usd,
            eta_s=round(ETA_START_S + to_ask / ETA_CALLS_PER_S, 1),
            command=shlex.join([*self.cli, *run_args]),
            expires_at=expires.isoformat(timespec="seconds"),
            key=key,
            max_usd_allowed=self._allowed(key),
        )

    def key_status(self) -> KeyStatus:
        """`tesselint key --json`: key presence and balance, never the key (§4.4, `/api/key`).

        A CLI that fails or prints something else gives `present: false` with the reason.
        """
        try:
            proc = self._cli_run(["key", "--json"], KEY_TIMEOUT_S)
        except ApiError as e:
            return _key_error(e.message)
        if proc.returncode != 0:
            return _key_error(_failure(proc, "key --json"))
        try:
            return _parse_key(_last_json_line(proc.stdout))
        except ValueError as e:
            return _key_error(f"unreadable output of key --json: {e}")

    # --- jobs -------------------------------------------------------------------------------------

    def start(self, estimate_id: str, max_usd: float, key: KeyStatus) -> Job:
        """Check hash (409), cap (422) and no running job (409), then spawn the run (§4.4).

        An unknown or expired estimate is 409 `estimate_stale` too. `key` should be fresh
        (`key_status()`); the cap must satisfy estimate ≤ max_usd ≤ min(ui_max_usd, remaining).
        The confirm word is the API's to check. A started estimate cannot start again.
        """
        with self._lock:
            pending = self._estimates.get(estimate_id)
            if pending is None:
                raise ApiError(ErrorCode.ESTIMATE_STALE, "no such estimate; estimate again")
            if time.monotonic() - pending.created >= ESTIMATE_TTL_S:
                del self._estimates[estimate_id]
                raise ApiError(ErrorCode.ESTIMATE_STALE, "the estimate expired; estimate again")
            if _source_digest(pending.source) != pending.source_sha256:
                raise ApiError(
                    ErrorCode.ESTIMATE_STALE,
                    "the source changed since the estimate; estimate again",
                )
            allowed = self._allowed(key)
            cap = float(max_usd)
            if not (
                math.isfinite(cap)
                and pending.usd - CAP_EPSILON <= cap <= allowed + CAP_EPSILON
                and cap >= 0
            ):
                raise ApiError(
                    ErrorCode.CAP_INVALID,
                    f"the cap must be between the estimate (US${pending.usd:.4f}) and"
                    f" US${allowed:.2f}, the UI limit or the key's remaining balance",
                )
            running = self._active_locked()
            if running is not None:
                raise ApiError(ErrorCode.JOB_RUNNING, f"job {running['id']} is still running")
            job = self._spawn(pending, cap)
            del self._estimates[estimate_id]
            return job

    def get(self, id: str) -> Job | None:
        """A job by id, re-attached by pid after a restart, else `lost` (§4.4)."""
        if not JOB_ID.fullmatch(id):
            return None
        with self._lock:
            doc = self._current(id)
            return None if doc is None else _public(doc)

    def cancel(self, id: str) -> Job:
        """SIGINT the job's process group, SIGTERM after 20 s; the run stays resumable (§4.4).

        An unknown id is 404 `not_found`; a job that already ended is returned as it is.
        """
        with self._lock:
            doc = self._current(id) if JOB_ID.fullmatch(id) else None
            if doc is None:
                raise ApiError(ErrorCode.NOT_FOUND, f"no job {id!r}")
            if doc["state"] != "running":
                return _public(doc)
            (self.jobs_dir / id / CANCEL_FILE).write_text(_now() + "\n", "utf-8")
            self._signal(id, doc["pid"], signal.SIGINT)
            timer = threading.Timer(CANCEL_GRACE_S, self._escalate, args=(id,))
            timer.daemon = True
            timer.start()
            return _public(doc)

    def active(self) -> Job | None:
        """The running job, if any (§4.4)."""
        with self._lock:
            doc = self._active_locked()
            return None if doc is None else _public(doc)

    def jobs(self) -> list[Job]:
        """Every job under the state dir, newest first (for `GET /api/jobs`)."""
        with self._lock:
            docs = [self._current(job_id) for job_id in self._job_ids()]
            return [_public(doc) for doc in docs if doc is not None]

    # --- internals --------------------------------------------------------------------------------

    def _scope_args(self, scope: RunScope) -> list[str]:
        """`--tier T`, `--epics E1,E2` and `--resume DIR`, checked so no value reads as a flag."""
        if scope.tier is not None and scope.epics is not None:
            raise ValueError("choose a tier or epics, not both")
        args: list[str] = []
        if scope.tier is not None:
            if scope.tier not in TIERS:
                raise ValueError(f"unknown tier {scope.tier!r} (expected {', '.join(TIERS)})")
            args += ["--tier", scope.tier]
        if scope.epics is not None:
            if not scope.epics or not all(EPIC_ID.fullmatch(e) for e in scope.epics):
                raise ValueError(f"epics must look like E1,E2: {list(scope.epics)!r}")
            args += ["--epics", ",".join(scope.epics)]
        if scope.resume is not None:
            args += ["--resume", str(self._resume_dir(scope.resume))]
        return args

    def _resume_dir(self, ref: str) -> Path:
        """The run directory of `ref`, only inside the runs root; else 404 `run_not_found`."""
        root = self.runs_root.resolve()
        if REF_PATTERN.fullmatch(ref):
            run_dir = root.joinpath(*ref.split(REF_SEP)).resolve()
            if run_dir.is_relative_to(root) and (run_dir / "run.json").is_file():
                return run_dir
        raise ApiError(ErrorCode.RUN_NOT_FOUND, f"no run {ref!r} to resume")

    def _allowed(self, key: KeyStatus) -> float:
        """min(ui_max_usd, remaining); 0 without a working key, ui_max_usd for an unlimited one."""
        if not key["present"] or key["error"] is not None:
            return 0.0
        remaining = key["remaining_usd"]
        if remaining is None:
            return self.ui_max_usd
        return max(0.0, min(self.ui_max_usd, remaining))

    def _forget_expired(self) -> None:
        now = time.monotonic()
        for estimate_id, pending in list(self._estimates.items()):
            if now - pending.created >= ESTIMATE_TTL_S:
                del self._estimates[estimate_id]

    def _child_env(self) -> dict[str, str]:
        """The server's environment (the CLI finds the key itself) plus the state dir."""
        env = dict(os.environ)
        env[STATE_DIR_ENV] = str(self.state_dir.resolve())
        return env

    def _cli_run(self, args: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
        """Run a short CLI command to completion; a timeout or spawn error is 502 `cli_failed`."""
        try:
            return subprocess.run(
                [*self.cli, *args],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=self._child_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ApiError(
                ErrorCode.CLI_FAILED, f"tesselint {args[0]} took longer than {timeout:g} s"
            ) from None
        except OSError as e:
            raise ApiError(
                ErrorCode.CLI_FAILED, f"could not run tesselint: {_redact(str(e))}"
            ) from None

    def _spawn(self, pending: _Pending, max_usd: float) -> Job:
        """Start `[*cli, run, …, --max-usd X, --out <runs>/ui, --progress-jsonl P]` (§4.4)."""
        job_id = f"job-{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, mode=0o700)
        argv = [
            *self.cli,
            *pending.run_args,
            "--max-usd",
            repr(max_usd),
            "--out",
            str((self.runs_root / UI_RUNS_DIR).resolve()),
            "--progress-jsonl",
            str((job_dir / PROGRESS_FILE).resolve()),
        ]
        log_fd = os.open(job_dir / LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                env=self._child_env(),
                start_new_session=True,
            )
        except OSError as e:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise ApiError(
                ErrorCode.CLI_FAILED, f"could not start tesselint run: {_redact(str(e))}"
            ) from None
        finally:
            os.close(log_fd)
        self._procs[job_id] = proc
        doc = JobFile(
            id=job_id,
            state="running",
            exit_code=None,
            message=None,
            run_ref=None,
            max_usd=max_usd,
            spent_usd=0.0,
            log_tail=[],
            phases=[],
            pid=proc.pid,
            argv=argv,
        )
        _write_json(job_dir / JOB_FILE, doc)
        name = f"tesselint-{job_id}"
        threading.Thread(target=self._monitor, args=(job_id,), name=name, daemon=True).start()
        return _public(doc)

    def _monitor(self, job_id: str) -> None:
        """Refresh `job.json` every POLL_INTERVAL_S until the job ends (§4.4 Poll)."""
        while True:
            time.sleep(POLL_INTERVAL_S)
            with self._lock:
                doc = self._current(job_id)
                if doc is None or doc["state"] != "running":
                    return

    def _escalate(self, job_id: str) -> None:
        """SIGTERM a cancelled job that is still running after CANCEL_GRACE_S."""
        with self._lock:
            doc = self._current(job_id)
            if doc is not None and doc["state"] == "running":
                self._signal(job_id, doc["pid"], signal.SIGTERM)

    def _signal(self, job_id: str, pid: int, sig: signal.Signals) -> None:
        """Signal the job's process group if it is still ours or still leads its group."""
        proc = self._procs.get(job_id)
        running = proc.poll() is None if proc is not None else _leads_group(pid)
        if running:
            with suppress(ProcessLookupError):
                os.killpg(pid, sig)

    def _job_ids(self) -> list[str]:
        if not self.jobs_dir.is_dir():
            return []
        names = (p.name for p in self.jobs_dir.iterdir() if p.is_dir())
        return sorted((n for n in names if JOB_ID.fullmatch(n)), reverse=True)

    def _active_locked(self) -> JobFile | None:
        for job_id in self._job_ids():
            doc = self._current(job_id)
            if doc is not None and doc["state"] == "running":
                return doc
        return None

    def _current(self, job_id: str) -> JobFile | None:
        """job.json, refreshed from the process, progress and log while it runs."""
        doc = self._load(job_id)
        if doc is None or doc["state"] != "running":
            return doc
        return self._refresh(job_id, doc)

    def _load(self, job_id: str) -> JobFile | None:
        path = self.jobs_dir / job_id / JOB_FILE
        try:
            data = json.loads(path.read_text("utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return cast(JobFile, data) if _job_file_ok(data, job_id) else None

    def _refresh(self, job_id: str, doc: JobFile) -> JobFile:
        job_dir = self.jobs_dir / job_id
        proc = self._procs.get(job_id)
        exit_code = proc.poll() if proc is not None else None
        alive = exit_code is None if proc is not None else _leads_group(doc["pid"])
        fresh = cast(JobFile, dict(doc))
        self._apply_progress(fresh, _read_events(job_dir / PROGRESS_FILE))
        if not alive:
            _scrub_log(job_dir / LOG_FILE)
            if exit_code is not None:
                _apply_exit(fresh, exit_code, (job_dir / CANCEL_FILE).exists(), job_dir / LOG_FILE)
            else:
                self._apply_run_status(fresh)
            self._procs.pop(job_id, None)
        fresh["log_tail"] = _log_tail(job_dir / LOG_FILE)
        if proc is None:
            if alive:
                return fresh  # re-attached and still running: its spawner writes job.json
            on_disk = self._load(job_id)
            if on_disk is not None and on_disk["state"] != "running":
                return on_disk  # its spawner recorded the real exit code first
        if fresh != doc:
            _write_json(job_dir / JOB_FILE, fresh)
        return fresh

    def _apply_progress(self, doc: JobFile, events: Sequence[Mapping[str, Any]]) -> None:
        """Phases, spend and run ref from `progress.jsonl` (§7.4 events)."""
        phases: dict[str, JobPhase] = {}
        for event in events:
            kind = event.get("event")
            if kind == "phase" and _phase_ok(event):
                name = event["phase"]
                phases[name] = JobPhase(name=name, done=event["done"], total=event["total"])
                doc["spent_usd"] = float(event["spent_usd"])
            elif kind == "run_dir" and isinstance(event.get("run_dir"), str):
                ref = self._ref_of(Path(event["run_dir"]))
                if ref is not None:
                    doc["run_ref"] = ref
        if phases:
            doc["phases"] = list(phases.values())

    def _apply_run_status(self, doc: JobFile) -> None:
        """A dead job this manager did not spawn: its run.json status, else `lost` (§4.4)."""
        outcome = RUN_STATUS_STATE.get(self._run_status(doc["run_ref"]) or "")
        if outcome is None:
            doc["state"], doc["message"] = "lost", LOST_MESSAGE
        else:
            doc["state"], doc["message"] = outcome

    def _run_status(self, ref: str | None) -> str | None:
        if ref is None:
            return None
        try:
            meta = json.loads(
                self.runs_root.joinpath(*ref.split(REF_SEP), "run.json").read_text("utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        status = meta.get("status") if isinstance(meta, dict) else None
        return status if isinstance(status, str) else None

    def _ref_of(self, run_dir: Path) -> str | None:
        """The ref of a run directory under the runs root, else None."""
        try:
            rel = run_dir.resolve().relative_to(self.runs_root.resolve())
        except (OSError, ValueError):
            return None
        ref = REF_SEP.join(rel.parts)
        return ref if REF_PATTERN.fullmatch(ref) else None


# --- outcome --------------------------------------------------------------------------------------


def _apply_exit(doc: JobFile, code: int, cancel_requested: bool, log_path: Path) -> None:
    """State and message from the exit code (§2 Re-run messages); 4 shows the CLI's line."""
    exit_code = 128 - code if code < 0 else code  # killed by signal n: report 128 + n
    doc["exit_code"] = exit_code
    if exit_code == EXIT_OK:
        doc["state"], doc["message"] = "done", None
    elif exit_code == EXIT_INTERRUPTED or (cancel_requested and exit_code > 128):
        doc["state"], doc["message"] = "cancelled", EXIT_MESSAGES[EXIT_INTERRUPTED]
    else:
        line = _error_line(_log_tail(log_path))
        if exit_code == EXIT_BAD_INPUT or exit_code not in EXIT_MESSAGES:
            message = line or _exit_text(exit_code)
        else:
            message = EXIT_MESSAGES[exit_code]
        doc["state"], doc["message"] = "failed", message


def _exit_text(exit_code: int) -> str:
    if exit_code > 128:
        with suppress(ValueError):
            return f"Stopped by {signal.Signals(exit_code - 128).name}"
    return f"tesselint exited with code {exit_code}"


def _failure(proc: subprocess.CompletedProcess[str], what: str) -> str:
    """The CLI's error line of a failed command, redacted, else its exit code."""
    line = _error_line(_redact(proc.stderr).splitlines())
    return line or f"tesselint {what} exited with code {proc.returncode}"


def _error_line(lines: Sequence[str]) -> str | None:
    """The last `tesselint: error: …` line, as `error: …`."""
    for line in reversed(lines):
        if line.startswith(ERROR_PREFIX + "error:"):
            return line.removeprefix(ERROR_PREFIX).strip()
    return None


# --- files ----------------------------------------------------------------------------------------


def _public(doc: JobFile) -> Job:
    """The §7.3 Job: job.json without pid and argv."""
    return Job(
        id=doc["id"],
        state=doc["state"],
        exit_code=doc["exit_code"],
        message=doc["message"],
        run_ref=doc["run_ref"],
        max_usd=doc["max_usd"],
        spent_usd=doc["spent_usd"],
        log_tail=list(doc["log_tail"]),
        phases=list(doc["phases"]),
    )


def _job_file_ok(data: Any, job_id: str) -> bool:
    if not isinstance(data, dict) or data.get("id") != job_id:
        return False
    if data.get("state") not in ("running", "done", "failed", "cancelled", "lost"):
        return False
    pid, argv = data.get("pid"), data.get("argv")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return False
    fields = ("exit_code", "message", "run_ref", "max_usd", "spent_usd", "log_tail", "phases")
    return all(name in data for name in fields)


def _write_json(path: Path, doc: Mapping[str, Any]) -> None:
    """Write `doc` to `path` through a `.tmp` and a rename, so readers never see half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(path)


def _read_events(path: Path) -> list[dict[str, Any]]:
    """The progress events so far; a line still being written is skipped."""
    try:
        text = path.read_text("utf-8", errors="replace")
    except FileNotFoundError:
        return []
    events = []
    for line in text.splitlines():
        with suppress(json.JSONDecodeError):
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
    return events


def _phase_ok(event: Mapping[str, Any]) -> bool:
    counts = (event.get("done"), event.get("total"))
    return (
        isinstance(event.get("phase"), str)
        and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts)
        and _is_usd(event.get("spent_usd"))
    )


def _log_tail(path: Path) -> list[str]:
    """The last LOG_TAIL_LINES non-blank lines of the CLI's output, redacted."""
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            start = max(0, size - LOG_TAIL_BYTES)
            fh.seek(start)
            data = fh.read()
    except FileNotFoundError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if start > 0:
        lines = lines[1:]  # the first line was cut
    return [_redact(line.rstrip()) for line in lines if line.strip()][-LOG_TAIL_LINES:]


def _scrub_log(path: Path) -> None:
    """Redact the finished job's whole log in place."""
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return
    clean = _redact(text)
    if clean != text:
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(clean)
        tmp.replace(path)


def _source_digest(source: Path) -> str:
    """sha256 of the source file's bytes (MISSING_SOURCE if there is none)."""
    try:
        return hashlib.sha256(source.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError):
        return MISSING_SOURCE


# --- parsing and helpers --------------------------------------------------------------------------


def _last_json_line(text: str) -> dict[str, Any]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("no output")
    data = json.loads(lines[-1])
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def _parse_estimate(stdout: str) -> tuple[int, int, float]:
    """to_ask, cached and usd of the `--estimate-json` line."""
    data = _last_json_line(stdout)
    to_ask, cached, usd = data.get("to_ask"), data.get("cached"), data.get("usd")
    counts_ok = all(
        isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (to_ask, cached)
    )
    if not counts_ok or not _is_usd(usd):
        raise ValueError("expected to_ask, cached and usd")
    return cast(int, to_ask), cast(int, cached), float(cast(float, usd))


def _parse_key(data: Mapping[str, Any]) -> KeyStatus:
    present, error = data.get("present"), data.get("error")
    if not isinstance(present, bool) or not (error is None or isinstance(error, str)):
        raise ValueError("expected present and error")
    amounts: dict[str, float | None] = {}
    for name in ("usage_usd", "limit_usd", "remaining_usd"):
        value = data.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
            raise ValueError(f"{name} is not a number")
        amounts[name] = None if value is None else float(value)
    return KeyStatus(
        present=present,
        usage_usd=amounts["usage_usd"],
        limit_usd=amounts["limit_usd"],
        remaining_usd=amounts["remaining_usd"],
        error=None if error is None else _redact(error),
    )


def _key_error(message: str) -> KeyStatus:
    return KeyStatus(
        present=False,
        usage_usd=None,
        limit_usd=None,
        remaining_usd=None,
        error=_redact(message),
    )


def _is_usd(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _leads_group(pid: int) -> bool:
    """A live process that still leads its own group: the job's CLI, not a reused pid."""
    try:
        return pid_alive(pid) and os.getpgid(pid) == pid
    except ProcessLookupError:
        return False


def _redact(text: str) -> str:
    return SECRET_PATTERN.sub(REDACTED, text).replace("sk-or-", REDACTED)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
