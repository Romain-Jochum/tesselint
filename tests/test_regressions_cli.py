"""Regressions for the CLI review: one-line errors, validation, env, help, output (DESIGN §7).

Every test drives `main(argv)` in-process from its own tmp_path with python-dotenv disabled, so
neither the real .env nor the real runs/ directory is reachable. Where only the CLI's handling of
a run matters, `pipeline.run` is replaced by `StubRun`; the tests marked `slow` run the real
pipeline on a planted synthetic source against FakeJev.
"""

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tesselint import cli, pipeline
from tesselint.config import TIERS, RunConfig
from tesselint.errors import InputError, UsageError
from tesselint.jev import JevClient, JevFatal
from tests.fakes import FakeJev
from tests.helpers_pipeline import (
    build_world,
    force_hf_offline,
    make_config,
    read_json,
    read_jsonl,
    run_pipeline,
)

ERROR_LINE = re.compile(r"^tesselint( \w+)?: error: ")
SUBCOMMANDS = ("parse", "run", "synth", "eval", "report")


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    force_hf_offline(monkeypatch)


def invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the CLI; return (exit code, stdout, stderr), mapping SystemExit as the script would."""
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    out, err = capsys.readouterr()
    return code, out, err


def one_error_line(err: str, *needles: str) -> str:
    """stderr holds exactly one `tesselint…: error:` line, no traceback; return it."""
    assert "Traceback" not in err
    errors = [line for line in err.splitlines() if ERROR_LINE.match(line)]
    assert len(errors) == 1, err
    missing = [needle for needle in needles if needle not in errors[0]]
    assert missing == [], errors[0]
    return errors[0]


def unset_later(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Unset `name` now and make sure a value the CLI sets is removed after the test."""
    monkeypatch.setenv(name, "")
    monkeypatch.delenv(name)


@dataclass
class StubRun:
    """Stands in for `pipeline.run`: records the config, announces the run dir, writes run.json."""

    status: str = "complete"
    raises: BaseException | None = None
    configs: list[RunConfig] = field(default_factory=list)

    async def __call__(
        self,
        cfg: RunConfig,
        client: JevClient,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> Path:
        self.configs.append(cfg)
        run_dir = cfg.out_root / "20260926-120000"
        run_dir.mkdir(parents=True, exist_ok=True)
        if progress is not None:
            progress(f"run dir: {run_dir}")
        if self.raises is not None:
            raise self.raises
        meta = {
            "status": self.status,
            "wall_s": 1.53,
            "max_usd": cfg.max_usd,
            "calls": {"live": 3, "cached": 2, "failed": 0, "retries": 0, "by_status": {}},
            "cost_usd": {
                "live": 0.0123,
                "cached_equivalent": 0.0,
                "preflight_estimate": 0.0301,
                "key_usage_delta": None,
            },
        }
        (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
        return run_dir

    @property
    def cfg(self) -> RunConfig:
        """The one config the stub was called with."""
        [cfg] = self.configs
        return cfg


def stub_run(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> StubRun:
    """Replace `pipeline.run` with a StubRun."""
    stub = StubRun(**kwargs)
    monkeypatch.setattr(pipeline, "run", stub)
    return stub


def planted_run_dir(root: Path) -> Path:
    """A directory that passes for a run: run.json and an empty pairs.jsonl."""
    run_dir = root / "planted-run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text("{}", encoding="utf-8")
    (run_dir / "pairs.jsonl").write_text("", encoding="utf-8")
    return run_dir


# H3 · bad input files are one `tesselint: error:` line and exit 4, never a traceback


def _bad_input_cases(tmp_path: Path) -> dict[str, list[str]]:
    run_dir = planted_run_dir(tmp_path)
    not_a_run = tmp_path / "not-a-run"
    not_a_run.mkdir()
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "incomplete.json").write_text('{"seed": 1, "n": 20}', encoding="utf-8")
    (tmp_path / "bad.jsonl").write_text("not json\n", encoding="utf-8")
    (tmp_path / "a-file").write_text("", encoding="utf-8")
    return {
        "parse-missing-source": ["parse", "--source", "nope.md"],
        "run-missing-source": ["run", "--source", "nope.md", "--dry-run"],
        "run-resume-a-file": ["run", "--resume", str(tmp_path / "a-file"), "--dry-run"],
        "eval-not-a-run": ["eval", str(not_a_run), "--truth", str(tmp_path / "bad.json")],
        "report-not-a-run": ["report", str(not_a_run)],
        "eval-truth-bad-json": ["eval", str(run_dir), "--truth", str(tmp_path / "bad.json")],
        "eval-truth-incomplete": [
            "eval",
            str(run_dir),
            "--truth",
            str(tmp_path / "incomplete.json"),
        ],
        "eval-labels-bad-jsonl": ["eval", str(run_dir), "--labels", str(tmp_path / "bad.jsonl")],
    }


BAD_INPUT_IDS = [
    "parse-missing-source",
    "run-missing-source",
    "run-resume-a-file",
    "eval-not-a-run",
    "report-not-a-run",
    "eval-truth-bad-json",
    "eval-truth-incomplete",
    "eval-labels-bad-jsonl",
]


@pytest.mark.parametrize("case", BAD_INPUT_IDS)
def test_bad_input_is_exit_4_with_one_error_line(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """H3: missing sources, non-run dirs and malformed truth/labels exit 4 in one line (§7)."""
    code, _, err = invoke(_bad_input_cases(tmp_path)[case], capsys)

    assert code == 4, err
    one_error_line(err)


SAMPLE_URL = "https://github.com/Romain-Jochum/tesselint/blob/main/tests/fixtures/sample_backlog.md"
NO_STORY_SOURCES = {
    "req-ids": "# Requirements\n\n- **REQ-1 Login.** A member signs in with an email link.\n",
    "plain-bullets": "## Stories\n\n- As a member, I book a tool for a two-hour slot.\n",
}
NO_STORY_COMMANDS = {
    "parse": ["parse", "--source", "reqs.md"],
    "run-dry-run": ["run", "--source", "reqs.md", "--dry-run", "--out", "runs"],
    "run-estimate": ["run", "--source", "reqs.md", "--estimate-json", "--out", "runs"],
    "import": ["import", "reqs.md", "--out", "stories.json"],
}


@pytest.mark.parametrize("source", list(NO_STORY_SOURCES))
@pytest.mark.parametrize("command", list(NO_STORY_COMMANDS))
def test_a_markdown_source_without_a_story_is_exit_4_with_the_expected_shape(
    command: str, source: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A1 M3: a file in another shape is one error line naming the shape, never an empty run."""
    (tmp_path / "reqs.md").write_text(NO_STORY_SOURCES[source], encoding="utf-8")

    code, out, err = invoke(NO_STORY_COMMANDS[command], capsys)

    assert code == 4, err
    one_error_line(
        err,
        "reqs.md",
        "### E<n>",
        "- **US-<n>.<m> Title.**",
        SAMPLE_URL,
        "other formats: convert to stories v1 JSON (docs/input/INPUT.md)",
    )
    assert out == ""
    assert list(tmp_path.glob("runs/*")) == []
    assert not (tmp_path / "stories.json").exists()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        pytest.param(InputError("2 unparsed story lines in stories.md"), 4, id="input-error"),
        pytest.param(UsageError("--resume needs a run directory"), 2, id="usage-error"),
        pytest.param(JevFatal("HTTP 401: No auth credentials found", status=401), 5, id="jev"),
    ],
)
def test_pipeline_errors_map_to_their_exit_code(
    error: Exception,
    code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """H1/M1 surfaced: a TesseLintError keeps its exit code and message; JevFatal is exit 5."""
    stub_run(monkeypatch, raises=error)

    got, _, err = invoke(["run", "--tier", "t1"], capsys)

    assert got == code
    one_error_line(err, str(error).split(":")[0])


@pytest.mark.parametrize(
    "argv",
    [["run", "--tier", "t1"], ["--debug", "run", "--tier", "t1"], ["run", "--debug"]],
    ids=["plain", "debug-before", "debug-after"],
)
def test_unexpected_error_is_exit_1_with_a_traceback_only_with_debug(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """H3: anything else is exit 1 and one line; `--debug` adds the full traceback (§7)."""
    stub_run(monkeypatch, raises=RuntimeError("boom"))

    code, _, err = invoke(argv, capsys)

    assert code == 1
    errors = [line for line in err.splitlines() if ERROR_LINE.match(line)]
    assert len(errors) == 1
    assert "boom" in errors[0]
    assert ("Traceback" in err) == ("--debug" in argv)


def test_ctrl_c_is_exit_130_with_a_resume_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """L5: Ctrl-C exits 130 and names the run dir announced by the pipeline's progress."""
    stub_run(monkeypatch, raises=KeyboardInterrupt())

    code, _, err = invoke(["run", "--tier", "t1", "--out", "runs"], capsys)

    assert code == 130
    line = one_error_line(err, "interrupted")
    run_dir = Path("runs") / "20260926-120000"
    assert line.endswith(f"interrupted; resume with: tesselint run --resume {run_dir}")


# H4 · numeric flags are validated by argparse


@pytest.mark.parametrize(
    ("argv", "flag"),
    [
        (["run", "--concurrency", "0"], "--concurrency"),
        (["run", "--concurrency", "-3"], "--concurrency"),
        (["run", "--concurrency", "x"], "--concurrency"),
        (["run", "--rate", "-1"], "--rate"),
        (["run", "--rate", "0"], "--rate"),
        (["run", "--rate", "inf"], "--rate"),
        (["run", "--prefilter-k", "0"], "--prefilter-k"),
        (["run", "--max-usd", "-1"], "--max-usd"),
        (["synth", "--out", "s", "--n", "-4"], "--n"),
        (["synth", "--out", "s", "--n", "0"], "--n"),
        (["eval", "r", "--make-labels", "l.jsonl", "--n", "-5"], "--n"),
        (["report", "r", "--merge", "7"], "--merge"),
        (["report", "r", "--cluster", "-1"], "--cluster"),
        (["report", "r", "--split", "1.5"], "--split"),
        (["report", "r", "--swap-gate", "nan"], "--swap-gate"),
    ],
    ids=lambda value: " ".join(value) if isinstance(value, list) else value,
)
def test_out_of_range_numbers_are_usage_errors(
    argv: list[str],
    flag: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """H4: non-positive counts and rates, negative caps and thresholds outside 0-1 exit 2."""
    stub = stub_run(monkeypatch)

    code, _, err = invoke(argv, capsys)

    assert code == 2
    one_error_line(err, flag)
    assert stub.configs == []


# M3, H5, L1, L6 · what `run` hands the pipeline and the client


def test_max_usd_comes_from_the_env_and_the_flag_wins(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M3: TESSELINT_MAX_USD is the --max-usd default; the flag overrides it (§7)."""
    monkeypatch.setenv("TESSELINT_MAX_USD", "0.25")
    from_env = stub_run(monkeypatch)
    assert invoke(["run", "--tier", "t1"], capsys)[0] == 0
    from_flag = stub_run(monkeypatch)
    assert invoke(["run", "--tier", "t1", "--max-usd", "0.5"], capsys)[0] == 0

    assert (from_env.cfg.max_usd, from_flag.cfg.max_usd) == (0.25, 0.5)


def test_a_bad_max_usd_env_value_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M3: an unusable TESSELINT_MAX_USD is exit 2 and names the variable."""
    monkeypatch.setenv("TESSELINT_MAX_USD", "lots")
    stub = stub_run(monkeypatch)

    code, _, err = invoke(["run", "--tier", "t1"], capsys)

    assert code == 2
    one_error_line(err, "TESSELINT_MAX_USD")
    assert stub.configs == []


def test_run_passes_tier_refresh_and_the_env_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """H5/L1: `--tier` sets cfg.tier and its epics, `--refresh` sets cfg.refresh; §7 env vars."""
    monkeypatch.setenv("TESSELINT_MODEL", "typesafe/jev-test")
    stub = stub_run(monkeypatch)

    code, _, err = invoke(["run", "--tier", "t2", "--refresh", "--dry-run"], capsys)

    assert code == 0, err
    cfg = stub.cfg
    assert (cfg.tier, cfg.epics, cfg.refresh, cfg.dry_run) == ("t2", TIERS["t2"], True, True)
    assert cfg.model == "typesafe/jev-test"
    assert cfg.base_url == os.environ["OPENROUTER_BASE_URL"]
    assert cfg.cache_dir == Path(os.environ["TESSELINT_CACHE_DIR"])
    assert cfg.source == Path(os.environ["TESSELINT_SOURCE"])


def test_epics_run_has_no_tier(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """L1: an `--epics` run records its epics and no tier."""
    stub = stub_run(monkeypatch)

    assert invoke(["run", "--epics", "E1,E3"], capsys)[0] == 0
    assert (stub.cfg.tier, stub.cfg.epics, stub.cfg.refresh) == (None, ("E1", "E3"), False)


def test_client_connection_pool_follows_concurrency(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Runtime L6: JevClient gets max_connections = --concurrency, so 32 in flight is 32."""
    seen: list[dict[str, Any]] = []

    def recording_client(api_key: str, **kwargs: Any) -> JevClient:
        seen.append(kwargs)
        return JevClient(api_key, **kwargs)

    monkeypatch.setattr(cli, "JevClient", recording_client)
    stub_run(monkeypatch)

    assert invoke(["run", "--tier", "t1", "--concurrency", "32"], capsys)[0] == 0
    assert [kwargs.get("max_connections") for kwargs in seen] == [32]


def test_hf_progress_bars_are_disabled_before_the_prefilter_loads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """L3: `run` sets HF_HUB_DISABLE_PROGRESS_BARS=1 unless the user set it."""
    unset_later(monkeypatch, "HF_HUB_DISABLE_PROGRESS_BARS")
    seen: list[str | None] = []

    async def spy(cfg: RunConfig, client: JevClient, **kwargs: Any) -> Path:
        seen.append(os.environ.get("HF_HUB_DISABLE_PROGRESS_BARS"))
        return await StubRun()(cfg, client, **kwargs)

    monkeypatch.setattr(pipeline, "run", spy)

    assert invoke(["run", "--tier", "t1"], capsys)[0] == 0
    assert seen == ["1"]


def test_dotenv_is_found_from_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """L4: `.env` is looked up from the cwd (like runs/ and the cache) and never overrides."""
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED")
    unset_later(monkeypatch, "TESSELINT_MAX_USD")
    monkeypatch.setenv("TESSELINT_MODEL", "from-the-environment")
    (tmp_path / ".env").write_text(
        "TESSELINT_MAX_USD=0.25\nTESSELINT_MODEL=from-dotenv\n", encoding="utf-8"
    )
    stub = stub_run(monkeypatch)

    assert invoke(["run", "--tier", "t1", "--dry-run"], capsys)[0] == 0
    assert (stub.cfg.max_usd, stub.cfg.model) == (0.25, "from-the-environment")


# M2 · `run` reports progress on stderr and one summary line on stdout


@pytest.mark.parametrize(
    ("status", "code"),
    [("complete", 0), ("partial_cap", 3), ("partial_errors", 6)],
)
def test_run_prints_a_summary_line_and_maps_its_status(
    status: str,
    code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """M2: progress goes to stderr, `<status> · … → RUN_DIR` to stdout; 3 and 6 explain why."""
    stub_run(monkeypatch, status=status)

    got, out, err = invoke(["run", "--tier", "t1", "--out", "runs"], capsys)

    run_dir = Path("runs") / "20260926-120000"
    assert got == code
    assert f"run dir: {run_dir}" in err.splitlines()
    assert out.splitlines() == [f"{status} · 3 live / 2 cached calls · $0.0123 · 1.5s → {run_dir}"]
    if code:
        one_error_line(err, "cap" if code == 3 else "failed")
    else:
        assert not [line for line in err.splitlines() if ERROR_LINE.match(line)]


# M5 · help, choices and exclusive flags


def test_version_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    """A1 S11: `--version` answers without a subcommand, for bug reports."""
    code, out, err = invoke(["--version"], capsys)

    assert (code, out, err) == (0, "tesselint 0.1.0\n", "")


def test_root_help_lists_env_vars_and_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    """M5: the root epilog names every §7 env var and exit code."""
    code, out, _ = invoke(["--help"], capsys)

    assert code == 0
    env_vars = (
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "TESSELINT_MODEL",
        "TESSELINT_SOURCE",
        "TESSELINT_CACHE_DIR",
        "TESSELINT_MAX_USD",
    )
    assert [name for name in env_vars if name not in out] == []
    exit_codes = re.findall(r"^\s+(\d+)\s+\S", out.split("exit codes")[-1], re.MULTILINE)
    assert set(exit_codes) >= {"0", "1", "2", "3", "4", "5", "6", "130"}
    assert "--debug" in out


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_every_subcommand_help_has_a_description_example_and_defaults(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """M5: each subcommand explains itself, shows an example and its defaults."""
    code, out, _ = invoke([command, "--help"], capsys)

    assert code == 0
    assert re.search(rf"^\s+tesselint {command}\b", out, re.MULTILINE), out
    assert "example" in out.lower()
    assert "(default: " in out


def test_parse_help_shows_a_default_from_a_deep_clone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A long TESSELINT_SOURCE wraps its own default; `--epics` still shows a static one."""
    deep = "/" + "a-deeply-nested-folder/" * 5 + "tesselint/tests/fixtures/sample_backlog.md"
    assert len(deep) >= 90
    monkeypatch.setenv("TESSELINT_SOURCE", deep)

    code, out, _ = invoke(["parse", "--help"], capsys)

    assert code == 0
    assert "(default: every epic)" in out


def test_run_help_shows_tier_choices_and_numeric_defaults(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """M5: `--tier` has choices; defaults such as --concurrency 16 are shown."""
    _, out, _ = invoke(["run", "--help"], capsys)

    flat = " ".join(out.split())
    assert "{t1,t2,t3}" in flat
    assert "(default: 16)" in flat
    assert "(default: 50.0)" in flat


HELP_ARGVS = [[], *([command] for command in (*SUBCOMMANDS, "serve", "import", "export", "key"))]


@pytest.mark.parametrize("argv", HELP_ARGVS, ids=lambda argv: "-".join(argv) or "root")
def test_help_examples_use_backlog_md_and_never_a_tier(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A1 M6: examples fit a stranger's file; the Markdown cut is described, not "section 4"."""
    code, out, _ = invoke([*argv, "--help"], capsys)

    flat = " ".join(out.split())
    examples = out.split("example")[-1]
    assert code == 0
    assert "--tier" not in examples
    assert re.search(r"\b[A-Z][A-Z_]*\.md\b", examples) is None  # no ALL_CAPS.md example file
    assert "section 4" not in flat
    if argv in ([], ["parse"], ["run"], ["import"]):
        assert "only the `## 4.` section if present" in flat
        assert "backlog.md" in examples


def test_tier_help_names_the_calibration_presets(capsys: pytest.CaptureFixture[str]) -> None:
    """A1 M6: `--tier` stays (DESIGN §8) but says whose epics its presets are."""
    _, out, _ = invoke(["run", "--help"], capsys)

    assert "presets for the calibration backlog; use --epics" in " ".join(out.split())


def test_a_tier_run_prints_its_epics_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A1 M6: a `--tier` run says which epics it keeps."""
    stub_run(monkeypatch)

    code, _, err = invoke(["run", "--tier", "t1"], capsys)

    assert code == 0, err
    assert "epics: E1,E2,E20" in err.splitlines()


def test_tier_and_epics_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M5: `--tier t1 --epics E3` is a usage error, not a silent t1."""
    stub = stub_run(monkeypatch)

    code, _, err = invoke(["run", "--tier", "t1", "--epics", "E3"], capsys)

    assert code == 2
    one_error_line(err, "--epics")
    assert stub.configs == []


@pytest.mark.parametrize(
    "modes",
    [[], ["--truth", "t.json", "--labels", "l.jsonl"]],
    ids=["none", "two"],
)
def test_eval_needs_exactly_one_mode(
    modes: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M5: `eval RUN` without a mode, or with two, is exit 2 instead of a silent no-op."""
    code, _, err = invoke(["eval", str(planted_run_dir(tmp_path)), *modes], capsys)

    assert code == 2
    one_error_line(err)


# M6 · parse output


def test_parse_counts_only_the_selected_epics_tombstones(
    real_section4: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M6: `--epics E1,E2,E20` counts E20's one tombstone, not all three."""
    code, out, _ = invoke(
        ["parse", "--source", str(real_section4), "--epics", "E1,E2,E20", "--json"], capsys
    )

    assert code == 0
    counts = json.loads(out)
    assert (counts["stories"], counts["tombstones"]) == (35, 1)
    assert counts["unparsed"] == []


def _broken_source(real_section4: Path, root: Path) -> tuple[Path, int]:
    """The §4 fixture behind a 5-line preamble, with a broken US-1.99 line; its 1-based line."""
    lines = real_section4.read_text("utf-8").splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.startswith("- **US-1.2 "))
    lines.insert(at, "- **US-1.99 Broken story [MVP] with no closing bold marker\n")
    preamble = ["# User stories\n", "\n", "## 3. Phases\n", "\n", "Some text.\n"]
    source = root / "broken.md"
    source.write_text("".join(preamble + lines), encoding="utf-8")
    return source, len(preamble) + at + 1


def test_parse_shows_unparsed_lines_with_source_line_numbers(
    real_section4: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M6: unparsed lines carry their line in the source file, then one error summary."""
    source, line_no = _broken_source(real_section4, tmp_path)

    code, _, err = invoke(["parse", "--source", str(source)], capsys)

    assert code == 4
    [shown] = [line for line in err.splitlines() if "US-1.99" in line]
    assert re.search(rf"(?<!\d){line_no}(?!\d)", shown), shown
    one_error_line(err, "unparsed")


def test_parse_json_reports_unparsed_lines_on_failure(
    real_section4: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M6: `--json` still prints its document when the parse gate fails."""
    source, line_no = _broken_source(real_section4, tmp_path)

    code, out, err = invoke(["parse", "--source", str(source), "--json"], capsys)

    assert code == 4
    report = json.loads(out)
    assert [row["line"] for row in report["unparsed"]] == [line_no]
    assert "US-1.99" in report["unparsed"][0]["text"]
    one_error_line(err, "unparsed")


# L5 · synth, report and eval say what they wrote


def test_synth_prints_what_it_wrote(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """L5: `synth` names stories.md and truth.json."""
    code, out, _ = invoke(["synth", "--out", "synth", "--n", "20"], capsys)

    assert code == 0
    assert str(Path("synth") / "stories.md") in out
    assert str(Path("synth") / "truth.json") in out


@pytest.mark.slow
@pytest.mark.anyio
async def test_report_and_eval_print_what_they_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """L5: `report`, `--make-labels` and `--labels` name their files; `--labels` writes
    RUN/calibration.json with the scores it prints (DESIGN §5)."""
    world = build_world(tmp_path)
    run_dir = await run_pipeline(make_config(world.source, tmp_path), FakeJev(world.answer))
    sheet = tmp_path / "labels.jsonl"

    report_code, report_out, _ = invoke(["report", str(run_dir), "--merge", "0.6"], capsys)
    make_code, make_out, _ = invoke(["eval", str(run_dir), "--make-labels", str(sheet)], capsys)
    rows = read_jsonl(sheet)
    for row in rows:
        if row.get("kind") == "pair":
            row["label"] = "none"
    sheet.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    labels_code, labels_out, labels_err = invoke(
        ["eval", str(run_dir), "--labels", str(sheet)], capsys
    )

    assert (report_code, make_code, labels_code) == (0, 0, 0), labels_err
    assert str(run_dir / "proposals.md") in report_out
    assert str(sheet) in make_out
    assert str(run_dir / "calibration.json") in labels_out + labels_err
    assert read_json(run_dir / "calibration.json") == json.loads(labels_out)


@pytest.mark.slow
def test_a_real_run_prints_progress_and_its_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M2 end to end: FakeJev run through `main`; progress on stderr, summary on stdout."""
    world = build_world(tmp_path)
    fake = FakeJev(world.answer)

    def fake_client(api_key: str, **kwargs: Any) -> JevClient:
        return JevClient(api_key, transport=fake.transport, **kwargs)

    monkeypatch.setattr(cli, "JevClient", fake_client)

    code, out, err = invoke(
        ["run", "--source", str(world.source), "--rate", "10000", "--out", "runs"], capsys
    )

    assert code == 0, err
    [run_dir] = list((tmp_path / "runs").iterdir())
    run = read_json(run_dir / "run.json")
    assert run["calls"]["live"] > 0
    assert any(line.startswith("run dir: ") for line in err.splitlines())
    [summary] = out.splitlines()
    assert summary.startswith(f"complete · {run['calls']['live']} live / ")
    assert summary.endswith(f"→ {Path('runs') / run_dir.name}")
