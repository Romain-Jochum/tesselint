"""`tesselint` CLI driven in-process through `main(argv)` (DESIGN §7).

`invoke` maps a SystemExit (argparse's --help and usage errors) to its code, as the console
script would. Tests that remove OPENROUTER_API_KEY also disable python-dotenv and leave the
project directory, so the real .env can never supply a key.
"""

import json
import re
from pathlib import Path

import pytest

from tesselint.cli import main
from tests.helpers_pipeline import force_hf_offline, read_json

ERROR_PREFIX = "tesselint: error:"


@pytest.fixture(autouse=True)
def _hf_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    force_hf_offline(monkeypatch)


def invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the CLI; return (exit code, stdout, stderr)."""
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    out, err = capsys.readouterr()
    return code, out, err


def without_api_key(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> None:
    """Unset the key, stop python-dotenv from loading .env and leave the project directory."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.chdir(workdir)


def has_word(text: str, word: str) -> bool:
    """`word` appears as a whole token (so `--prefilter` does not match `--prefilter-k`)."""
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text) is not None


def reports_count(text: str, count: int, noun: str) -> bool:
    """`text` states `count` next to `noun`, as in `176 stories` or `"stories": 176`."""
    number = rf"(?<![\w.]){count}(?![\w.])"
    near = r"\W+(?:\w+\W+)?"
    pattern = rf"{number}{near}{noun}|{noun}\w*{near}{number}"
    return re.search(pattern, text, re.IGNORECASE) is not None


HELP_CASES = [
    pytest.param([], ("parse", "run", "synth", "eval", "report"), id="root"),
    pytest.param(["parse"], ("--source", "--epics", "--json"), id="parse"),
    pytest.param(
        ["run"],
        (
            "--source",
            "--tier",
            "--epics",
            "--out",
            "--resume",
            "--prefilter",
            "--prefilter-k",
            "--no-swap",
            "--render",
            "--concurrency",
            "--rate",
            "--max-usd",
            "--refresh",
            "--dry-run",
        ),
        id="run",
    ),
    pytest.param(["synth"], ("--out", "--seed", "--n"), id="synth"),
    pytest.param(["eval"], ("--truth", "--labels", "--make-labels", "--n"), id="eval"),
    pytest.param(["report"], ("--merge", "--cluster", "--split", "--swap-gate"), id="report"),
]


@pytest.mark.parametrize(("command", "words"), HELP_CASES)
def test_help_exits_0_and_lists_every_flag(
    command: list[str], words: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    """--help on the root and on every subcommand exits 0 and names its options (DESIGN §7)."""
    code, out, _ = invoke([*command, "--help"], capsys)

    assert code == 0
    assert out.startswith(" ".join(["usage: tesselint", *command]))
    assert [w for w in words if not has_word(out, w)] == []


def test_run_without_api_key_exits_5_with_a_one_line_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing OPENROUTER_API_KEY is exit 5 with one stderr line, no traceback (DESIGN §7)."""
    without_api_key(monkeypatch, tmp_path)

    code, _, err = invoke(["run", "--tier", "t1", "--out", str(tmp_path / "runs")], capsys)

    assert code == 5
    lines = err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(ERROR_PREFIX)
    assert "OPENROUTER_API_KEY" in lines[0]
    assert "Traceback" not in err


@pytest.mark.parametrize(
    ("argv", "culprit"),
    [
        pytest.param(["run", "--tier", "t9", "--dry-run"], "t9", id="run-unknown-tier"),
        pytest.param(["run", "--epics", "E1,E999", "--dry-run"], "E999", id="run-unknown-epic"),
        pytest.param(["parse", "--epics", "E999"], "E999", id="parse-unknown-epic"),
    ],
)
def test_unknown_tier_or_epic_is_a_usage_error(
    argv: list[str],
    culprit: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown tier or epic exits 2 with a `tesselint...: error:` line naming it (DESIGN §7)."""
    monkeypatch.chdir(tmp_path)  # the default --out runs/ lands in tmp_path

    code, _, err = invoke(argv, capsys)

    assert code == 2
    [error] = [line for line in err.splitlines() if re.match(r"tesselint( \w+)?: error:", line)]
    assert culprit in error
    assert "Traceback" not in err


@pytest.mark.parametrize("extra", [[], ["--json"]], ids=["text", "json"])
def test_parse_prints_the_counts_of_the_real_section(
    extra: list[str], real_section4: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`parse` on the frozen §4 reports 176 stories and 3 tombstones and exits 0 (DESIGN §7)."""
    code, out, _ = invoke(["parse", "--source", str(real_section4), *extra], capsys)

    assert code == 0
    assert reports_count(out, 176, "stor")
    assert reports_count(out, 3, "tombstone")
    if extra:
        json.loads(out)


def test_parse_selects_epics_by_heading_or_id(
    real_section4: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--epics E1,E2,E20` keeps the 35 stories of T1' (DESIGN §4, §8 select_epics)."""
    code, out, _ = invoke(["parse", "--source", str(real_section4), "--epics", "E1,E2,E20"], capsys)

    assert code == 0
    assert reports_count(out, 35, "stor")


def test_parse_exits_4_on_an_unparseable_story_line(
    real_section4: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `- **US-` line that is neither a story nor a tombstone is exit 4, and is shown (§7)."""
    lines = real_section4.read_text("utf-8").splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.startswith("- **US-1.2 "))
    lines.insert(at, "- **US-1.99 Broken story [MVP] with no closing bold marker\n")
    broken = tmp_path / "broken.md"
    broken.write_text("".join(lines), encoding="utf-8")

    code, out, err = invoke(["parse", "--source", str(broken)], capsys)

    assert code == 4
    assert "US-1.99" in out + err


def test_synth_writes_the_same_bytes_for_the_same_seed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`synth` writes stories.md and truth.json, byte-identical for one seed (DESIGN §6, §7)."""
    runs = {name: tmp_path / name for name in ("first", "again", "other")}
    seeds = {"first": "7", "again": "7", "other": "8"}

    codes = [
        invoke(["synth", "--out", str(runs[name]), "--seed", seeds[name], "--n", "20"], capsys)[0]
        for name in runs
    ]
    written = {
        name: ((d / "stories.md").read_bytes(), (d / "truth.json").read_bytes())
        for name, d in runs.items()
    }
    parse_code, parse_out, _ = invoke(
        ["parse", "--source", str(runs["first"] / "stories.md")], capsys
    )

    assert codes == [0, 0, 0]
    assert written["first"] == written["again"]
    assert written["first"][0] != written["other"][0]
    truth = read_json(runs["first"] / "truth.json")
    assert (truth["generator"], truth["seed"], truth["n"]) == ("tesselint-synth/1", 7, 20)
    assert parse_code == 0, "every planted parser variant must parse"
    assert reports_count(parse_out, 20, "stor")


@pytest.mark.slow
def test_run_dry_run_needs_no_api_key(
    real_section4: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`run --dry-run` works without a key, calls nothing and writes the estimate (DESIGN §7)."""
    monkeypatch.setenv("TESSELINT_SOURCE", str(real_section4))
    without_api_key(monkeypatch, tmp_path)
    out_root = tmp_path / "runs"

    code, _, err = invoke(["run", "--dry-run", "--tier", "t1", "--out", str(out_root)], capsys)

    assert code == 0, err
    [run_dir] = list(out_root.iterdir())
    run = read_json(run_dir / "run.json")
    assert (run["n_stories"], run["calls"]["live"]) == (35, 0)
    assert 0.015 < run["cost_usd"]["preflight_estimate"] < 0.05  # T1 is about US$0.03 (§4)


@pytest.mark.parametrize("argv", [["parse"], ["run", "--tier", "t1", "--dry-run"]])
def test_no_source_is_a_usage_error(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """There is no default source: without --source or TESSELINT_SOURCE, parse and run exit 2."""
    monkeypatch.delenv("TESSELINT_SOURCE")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    code, out, err = invoke(argv, capsys)

    assert (code, out) == (2, "")
    assert err == "tesselint: error: no source: pass --source or set TESSELINT_SOURCE\n"
