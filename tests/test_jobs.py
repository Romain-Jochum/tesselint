"""JobManager: estimate, start, poll, cancel and re-attach `tesselint run` (docs/ui/DESIGN.md §4.4).

Every spawn goes to the fake CLI (tests/fake_cli.py), which logs its argv, pid, session and the
signals it gets. After each test the log must show no spending run without `--max-usd` or without
an earlier estimate of the same argv.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tesselint.protocol import KeyStatus
from tesselint.server import jobs
from tesselint.server.errors import ApiError, ErrorCode
from tesselint.server.jobs import EXIT_MESSAGES, UI_RUNS_DIR, JobManager, RunScope
from tesselint.server.payloads import Estimate, Job
from tests.fake_cli import (
    fake_cli_argv,
    flag_value,
    invocations,
    is_estimate,
    is_job_run,
    kill_leftovers,
    read_log,
    scope_argv,
    spawn_violations,
    wait_for,
)
from tests.test_server_api import MD_SOURCE, assert_shape

KEY = KeyStatus(present=True, usage_usd=0.88, limit_usd=2.0, remaining_usd=1.12, error=None)
ESTIMATE_USD = 0.05  # the fake's default FAKE_CLI_USD


@dataclass(frozen=True)
class Env:
    state: Path
    runs: Path
    source: Path
    log: Path

    def manager(self, ui_max_usd: float = 1.0) -> JobManager:
        return JobManager(self.state, fake_cli_argv(), self.runs, ui_max_usd)

    def job_runs(self) -> list[dict[str, Any]]:
        return [r for r in invocations(self.log) if is_job_run(r["argv"])]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    source = tmp_path / "stories.md"
    shutil.copyfile(MD_SOURCE, source)
    setup = Env(tmp_path / "state", tmp_path / "runs", source, tmp_path / "fake_cli.log")
    setup.state.mkdir()
    setup.runs.mkdir()
    monkeypatch.setenv("FAKE_CLI_LOG", str(setup.log))
    monkeypatch.setenv("FAKE_CLI_MODE", "ok")
    yield setup
    kill_leftovers(setup.log)
    assert spawn_violations(setup.log) == []


def finished(manager: JobManager, job_id: str, timeout: float = 20.0) -> Job:
    """Poll `get` until the job leaves `running`."""

    def fetch() -> Job:
        job = manager.get(job_id)
        assert job is not None, f"job {job_id} vanished"
        return job

    return wait_for(fetch, lambda job: job["state"] != "running", timeout)


def started_phase(manager: JobManager, job_id: str) -> Job:
    """Wait until a running job reports progress (the fake's first phase event)."""

    def fetch() -> Job:
        job = manager.get(job_id)
        assert job is not None
        return job

    return wait_for(fetch, lambda job: bool(job["phases"]) and job["state"] == "running")


def signals(env: Env) -> list[str]:
    return [r["signal"] for r in read_log(env.log) if "signal" in r]


# --- estimate -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "flags", "ui_max_usd", "allowed"),
    [
        (RunScope(tier="t1"), ["--tier", "t1"], 0.2, 0.2),
        (RunScope(epics=("E1", "E2")), ["--epics", "E1,E2"], 5.0, 1.12),
    ],
)
def test_estimate_relays_the_cli_line_and_the_allowed_cap(
    env: Env, scope: RunScope, flags: list[str], ui_max_usd: float, allowed: float
) -> None:
    """§4.4 Estimate: `run --estimate-json` for the source and scope; cap min(ui_max, remaining)."""
    estimate = env.manager(ui_max_usd).estimate(env.source, scope)

    assert_shape(estimate, Estimate)
    assert (estimate["to_ask"], estimate["cached"], estimate["usd"]) == (12, 30, ESTIMATE_USD)
    assert estimate["key"] == KEY
    assert estimate["max_usd_allowed"] == pytest.approx(allowed)
    assert str(env.source) in estimate["command"]
    [call] = [r["argv"] for r in invocations(env.log) if is_estimate(r["argv"])]
    assert flag_value(call, "--source") == str(env.source)
    assert all(flag in call for flag in flags)
    assert env.job_runs() == []
    assert not (env.runs / UI_RUNS_DIR).exists()


def test_estimate_cli_failure_is_502_with_the_cli_line(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.4, §7.2: a failing `--estimate-json` (exit 4) is 502 `cli_failed` naming the problem."""
    monkeypatch.setenv("FAKE_CLI_MODE", "bad_input")

    with pytest.raises(ApiError) as excinfo:
        env.manager().estimate(env.source, RunScope(tier="t1"))

    assert excinfo.value.code is ErrorCode.CLI_FAILED
    assert "unparsed" in excinfo.value.message


# --- start ----------------------------------------------------------------------------------------


def test_start_spawns_the_estimated_argv_with_cap_out_and_progress(env: Env) -> None:
    """§4.4 Start: the estimate's argv + `--max-usd X --out <runs>/ui --progress-jsonl P`."""
    manager = env.manager()
    estimate = manager.estimate(env.source, RunScope(tier="t1"))

    job = manager.start(estimate["id"], 0.06, KEY)

    assert_shape(job, Job)
    assert job["state"] in ("running", "done")
    assert job["max_usd"] == 0.06
    finished(manager, job["id"])
    [record] = env.job_runs()
    argv = record["argv"]
    [estimate_argv] = [r["argv"] for r in invocations(env.log) if is_estimate(r["argv"])]
    assert scope_argv(argv) == scope_argv(estimate_argv)
    assert float(flag_value(argv, "--max-usd") or "nan") == 0.06
    assert Path(flag_value(argv, "--out") or "").resolve() == (env.runs / UI_RUNS_DIR).resolve()
    progress = Path(flag_value(argv, "--progress-jsonl") or "").resolve()
    assert progress.is_relative_to(env.state.resolve())
    assert record["sid"] == record["pid"], "the run must start in a new session"


@pytest.mark.parametrize(
    ("mode", "state", "exit_code", "message"),
    [
        ("ok", "done", 0, None),
        ("cap", "failed", 3, EXIT_MESSAGES[3]),
        ("auth", "failed", 5, EXIT_MESSAGES[5]),
        ("bad_input", "failed", 4, "1 unparsed story line(s)"),
    ],
)
def test_finished_job_reports_state_progress_and_message(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    state: str,
    exit_code: int,
    message: str | None,
) -> None:
    """§2 Re-run messages, §4.4 Poll: exit code, message (4: the CLI's line), job.json on disk."""
    manager = env.manager()
    estimate = manager.estimate(env.source, RunScope(tier="t1"))
    monkeypatch.setenv("FAKE_CLI_MODE", mode)

    job = finished(manager, manager.start(estimate["id"], 0.1, KEY)["id"])

    assert (job["state"], job["exit_code"]) == (state, exit_code)
    if message is not None:
        assert job["message"] is not None and message in job["message"]
    stored = json.loads((env.state / "jobs" / job["id"] / "job.json").read_text("utf-8"))
    assert (stored["state"], stored["exit_code"]) == (state, exit_code)
    assert stored["pid"] == env.job_runs()[0]["pid"]
    assert stored["argv"][-len(env.job_runs()[0]["argv"]) :] == env.job_runs()[0]["argv"]
    if mode == "ok":
        [run_dir] = (env.runs / UI_RUNS_DIR).iterdir()
        assert job["run_ref"] == f"{UI_RUNS_DIR}~{run_dir.name}"
        phases = {p["name"]: (p["done"], p["total"]) for p in job["phases"]}
        assert phases == {"fwd": (12, 12), "atomic": (6, 6), "swap": (4, 4)}
        assert job["spent_usd"] == pytest.approx(ESTIMATE_USD)
        assert job["log_tail"]


def test_start_refuses_an_estimate_whose_source_changed(env: Env) -> None:
    """§4.4 Start: the source hash is checked first; an edit since the estimate is 409."""
    manager = env.manager()
    estimate = manager.estimate(env.source, RunScope(tier="t1"))
    with env.source.open("a", encoding="utf-8") as fh:
        fh.write("\n")

    with pytest.raises(ApiError) as excinfo:
        manager.start(estimate["id"], 99.0, KEY)  # also an invalid cap: the hash comes first

    assert excinfo.value.code is ErrorCode.ESTIMATE_STALE
    assert env.job_runs() == []


@pytest.mark.parametrize(
    ("ui_max_usd", "remaining", "max_usd"),
    [
        pytest.param(1.0, 1.12, ESTIMATE_USD - 0.01, id="below-estimate"),
        pytest.param(0.2, 1.12, 0.3, id="above-ui-max"),
        pytest.param(1.0, 0.1, 0.15, id="above-remaining"),
        pytest.param(1.0, 0.0, 0.06, id="no-balance"),
        pytest.param(1.0, 1.12, float("nan"), id="nan"),
        pytest.param(1.0, 1.12, -1.0, id="negative"),
    ],
)
def test_start_refuses_a_cap_outside_estimate_to_allowed(
    env: Env, ui_max_usd: float, remaining: float, max_usd: float
) -> None:
    """§2 Confirm, §4.4: estimate ≤ max_usd ≤ min(ui_max_usd, remaining), else 422 `cap_invalid`."""
    manager = env.manager(ui_max_usd)
    estimate = manager.estimate(env.source, RunScope(tier="t1"))
    key = KeyStatus(
        present=True, usage_usd=2.0 - remaining, limit_usd=2.0, remaining_usd=remaining, error=None
    )

    with pytest.raises(ApiError) as excinfo:
        manager.start(estimate["id"], max_usd, key)

    assert excinfo.value.code is ErrorCode.CAP_INVALID
    assert env.job_runs() == []


@pytest.mark.parametrize("max_usd", [ESTIMATE_USD, 0.2], ids=["at-estimate", "at-ui-max"])
def test_start_accepts_a_cap_on_either_bound(env: Env, max_usd: float) -> None:
    """§4.4: both bounds are inclusive."""
    manager = env.manager(0.2)
    estimate = manager.estimate(env.source, RunScope(tier="t1"))

    job = manager.start(estimate["id"], max_usd, KEY)

    assert finished(manager, job["id"])["state"] == "done"
    assert len(env.job_runs()) == 1


@pytest.mark.parametrize("case", ["unknown id", "expired"])
def test_start_without_a_live_estimate_spawns_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """§4.4: a run is never spawned without a prior estimate kept for ESTIMATE_TTL_S.

    `expired` sets `jobs.ESTIMATE_TTL_S` to 0, so the module constant must be read at call time.
    """
    manager = env.manager()
    if case == "expired":
        monkeypatch.setattr(jobs, "ESTIMATE_TTL_S", 0.0)
        estimate_id = manager.estimate(env.source, RunScope(tier="t1"))["id"]
    else:
        estimate_id = "no-such-estimate"

    with pytest.raises(ApiError):
        manager.start(estimate_id, 0.1, KEY)

    assert env.job_runs() == []


def test_one_job_at_a_time(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """§4.4 Start: while a job runs, another start is 409 `job_running`."""
    monkeypatch.setenv("FAKE_CLI_MODE", "slow")
    manager = env.manager()
    first = manager.start(manager.estimate(env.source, RunScope(tier="t1"))["id"], 0.1, KEY)
    second_estimate = manager.estimate(env.source, RunScope(tier="t2"))

    with pytest.raises(ApiError) as excinfo:
        manager.start(second_estimate["id"], 0.1, KEY)

    assert excinfo.value.code is ErrorCode.JOB_RUNNING
    active = manager.active()
    assert active is not None and active["id"] == first["id"]
    assert len(env.job_runs()) == 1


# --- cancel and restart ---------------------------------------------------------------------------


def test_cancel_sends_sigint_and_leaves_a_resumable_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.4 Cancel: SIGINT; the CLI writes `interrupted`, exits 130; the job is `cancelled`."""
    monkeypatch.setenv("FAKE_CLI_MODE", "slow")
    manager = env.manager()
    job = manager.start(manager.estimate(env.source, RunScope(tier="t1"))["id"], 0.1, KEY)
    started_phase(manager, job["id"])

    manager.cancel(job["id"])
    final = finished(manager, job["id"])

    assert (final["state"], final["exit_code"]) == ("cancelled", 130)
    assert final["message"] == EXIT_MESSAGES[130]
    assert signals(env) == ["SIGINT"]
    [run_dir] = (env.runs / UI_RUNS_DIR).iterdir()
    assert json.loads((run_dir / "run.json").read_text("utf-8"))["status"] == "interrupted"
    assert final["run_ref"] == f"{UI_RUNS_DIR}~{run_dir.name}"
    assert manager.active() is None


def test_cancel_escalates_to_sigterm_after_the_grace_period(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.4 Cancel: SIGTERM follows when SIGINT is ignored (CANCEL_GRACE_S, read at call time)."""
    monkeypatch.setenv("FAKE_CLI_MODE", "slow")
    monkeypatch.setenv("FAKE_CLI_IGNORE_SIGINT", "1")
    monkeypatch.setattr(jobs, "CANCEL_GRACE_S", 0.5)
    manager = env.manager()
    job = manager.start(manager.estimate(env.source, RunScope(tier="t1"))["id"], 0.1, KEY)
    started_phase(manager, job["id"])

    manager.cancel(job["id"])
    final = finished(manager, job["id"], timeout=15.0)

    assert final["state"] == "cancelled"
    assert signals(env) == ["SIGINT", "SIGTERM"]


def test_a_new_manager_reattaches_to_a_running_job(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.4 Restart: a restarted server finds the live pid and can still cancel the job."""
    monkeypatch.setenv("FAKE_CLI_MODE", "slow")
    first = env.manager()
    job = first.start(first.estimate(env.source, RunScope(tier="t1"))["id"], 0.1, KEY)
    started_phase(first, job["id"])

    restarted = env.manager()

    seen = restarted.get(job["id"])
    assert seen is not None and seen["state"] == "running"
    active = restarted.active()
    assert active is not None and active["id"] == job["id"]
    restarted.cancel(job["id"])
    assert finished(restarted, job["id"])["state"] == "cancelled"
    assert signals(env) == ["SIGINT"]


def dead_pid() -> int:
    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        check=True,
        capture_output=True,
        text=True,
    )
    pid = int(proc.stdout)
    assert not _alive(pid)
    return pid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize(("run_json_status", "state"), [("complete", "done"), (None, "lost")])
def test_a_new_manager_resolves_a_dead_job_from_run_json(
    env: Env, run_json_status: str | None, state: str
) -> None:
    """§4.4 Restart: a dead pid means read `run.json`; without one the job is `lost`."""
    run_id = "20260926-120000"
    run_dir = env.runs / UI_RUNS_DIR / run_id
    job_dir = env.state / "jobs" / "job-planted"
    job_dir.mkdir(parents=True)
    if run_json_status is not None:
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            json.dumps({"run_id": run_id, "status": run_json_status}), "utf-8"
        )
    progress = job_dir / "progress.jsonl"
    progress.write_text(json.dumps({"event": "run_dir", "run_dir": str(run_dir)}) + "\n", "utf-8")
    planted = {
        "id": "job-planted",
        "state": "running",
        "exit_code": None,
        "message": None,
        "run_ref": f"{UI_RUNS_DIR}~{run_id}" if run_json_status else None,
        "max_usd": 0.1,
        "spent_usd": 0.0,
        "log_tail": [],
        "phases": [],
        "pid": dead_pid(),
        "argv": [*fake_cli_argv(), "run", "--source", str(env.source), "--max-usd", "0.1"],
    }
    (job_dir / "job.json").write_text(json.dumps(planted), "utf-8")

    job = env.manager().get("job-planted")

    assert job is not None and job["state"] == state
    assert env.manager().active() is None
