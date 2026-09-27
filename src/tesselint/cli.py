"""The `tesselint` command line (DESIGN §7).

`main` parses the flags, runs one subcommand and turns every failure into one
`tesselint: error: …` line on stderr plus an exit code; `--debug` adds the traceback.

`jev` (and httpx), `pipeline`, `workers` and `dotenv` are imported inside the commands that need
them: `tesselint serve` runs this module and must never load them (docs/ui/DESIGN.md §1).
"""

import argparse
import asyncio
import dataclasses
import json
import math
import os
import sys
import tempfile
import traceback
from argparse import ArgumentParser
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tesselint import __version__, inputv1
from tesselint.config import (
    CALIBRATED_THRESHOLDS,
    DEFAULT_OUT_ROOT,
    DEFAULT_SERVE_HOST,
    DEFAULT_SERVE_PORT,
    DEFAULT_UI_MAX_USD,
    ENV_DEFAULTS,
    NO_SOURCE,
    TIERS,
    RunConfig,
    env,
    env_source,
)
from tesselint.errors import InputError, TesseLintError, UsageError
from tesselint.evaluate import load_truth, make_label_sheet, score_labels, score_synthetic
from tesselint.models import ParseResult, Tombstone
from tesselint.parse import is_v1, no_story_message, parse_section, select_epics, take_snapshot
from tesselint.protocol import KeyStatus
from tesselint.synth import Truth, generate, generate_v1, truth_to_dict

if TYPE_CHECKING:
    from tesselint.jev import JevClient as Client
    from tesselint.jev import KeyInfo

VALID_EPICS = frozenset(f"E{i}" for i in range(1, 24))
DEFAULT_THRESHOLDS = CALIBRATED_THRESHOLDS


class ExitCode(IntEnum):
    """Process exit codes (DESIGN §7)."""

    OK = 0
    UNEXPECTED = 1
    USAGE = 2
    SPEND_CAP = 3
    BAD_INPUT = 4
    JEV_AUTH = 5
    TOO_MANY_FAILURES = 6
    INTERRUPTED = 130


# run.json `status` values that are not a success (DESIGN §2.7, §11).
STATUS_EXIT = {
    "partial_cap": ExitCode.SPEND_CAP,
    "partial_errors": ExitCode.TOO_MANY_FAILURES,
    "interrupted": ExitCode.INTERRUPTED,
}

_ENV_LINES = "\n".join(f"  {name:<21} default {value}" for name, value in ENV_DEFAULTS.items())
ROOT_EPILOG = f"""\
environment (from the process, then ./.env, which never overrides it; `serve` never reads .env):
  OPENROUTER_API_KEY    required by `run` without --dry-run, and by `key`; never printed
  TESSELINT_SOURCE      the --source of `parse` and `run` when the flag is absent; no default
{_ENV_LINES}
  runs/, the cache, the state dir and .env are relative to the current directory.

exit codes:
  0    ok
  1    unexpected error (full traceback with --debug)
  2    usage: a bad flag, tier or epic
  3    spend cap: the estimate or the spend reached --max-usd
  4    bad input: unparsed story lines, a missing file, not a run directory
  5    Jev refused: missing or wrong key, no credits, HTTP 400
  6    too many failed Jev calls
  130  interrupted (Ctrl-C)

example:
  tesselint parse --source backlog.md && tesselint run --source backlog.md --dry-run
"""


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Shows each flag's default (unless None or False); keeps description and epilog lines."""

    def _get_help_string(self, action: argparse.Action) -> str | None:
        if action.default is None or action.default is False:
            return action.help
        return super()._get_help_string(action)


# argparse types: a bad value is a usage error (exit 2) that names the flag.


def _positive_int(text: str) -> int:
    """A whole number >= 1."""
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a whole number >= 1, got {text!r}")
    return value


def _float_type(expected: str, ok: Callable[[float], bool]) -> Callable[[str], float]:
    """A type accepting finite floats for which `ok` holds."""

    def parse(text: str) -> float:
        try:
            value = float(text)
        except ValueError:
            value = math.nan
        if not (math.isfinite(value) and ok(value)):
            raise argparse.ArgumentTypeError(f"expected {expected}, got {text!r}")
        return value

    return parse


_positive_float = _float_type("a number > 0", lambda v: v > 0)
_probability = _float_type("a number from 0 to 1", lambda v: 0 <= v <= 1)
_usd = _float_type("a number >= 0 (from --max-usd or TESSELINT_MAX_USD)", lambda v: v >= 0)
_ui_usd = _float_type("a number >= 0 (US$)", lambda v: v >= 0)


def _port(text: str) -> int:
    """A TCP port, 1 to 65535."""
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"expected a port from 1 to 65535, got {text!r}")
    return value


def _epics(text: str) -> tuple[str, ...]:
    """Comma-separated epic IDs, e.g. E1,E2,E20."""
    epics = tuple(e.strip().upper() for e in text.split(",") if e.strip())
    unknown = [e for e in epics if e not in VALID_EPICS]
    if unknown or not epics:
        shown = ", ".join(unknown) or repr(text)
        raise argparse.ArgumentTypeError(f"unknown epic: {shown} (expected E1 to E23, e.g. E1,E2)")
    return epics


def build_parser() -> argparse.ArgumentParser:
    """Build the parser for tesselint and its subcommands; defaults read the env (DESIGN §7)."""
    parser = argparse.ArgumentParser(
        prog="tesselint",
        description=(
            "Find duplicate, overlapping and bundled user stories in a Markdown backlog (only\n"
            "the `## 4.` section if present) or a stories v1 JSON file by asking Jev about\n"
            "every pair. TesseLint proposes merges and splits in runs/<id>/proposals.md; it\n"
            "never edits the source."
        ),
        epilog=ROOT_EPILOG,
        formatter_class=_HelpFormatter,
    )
    parser.add_argument("--debug", action="store_true", help="print tracebacks of errors")
    parser.add_argument("--version", action="version", version=f"tesselint {__version__}")
    debug = argparse.ArgumentParser(add_help=False)
    debug.add_argument(
        "--debug", action="store_true", default=argparse.SUPPRESS, help="print tracebacks of errors"
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True, metavar="COMMAND")

    def command(name: str, summary: str, description: str, *examples: str) -> ArgumentParser:
        lines = "\n".join(f"  {example}" for example in examples)
        return subparsers.add_parser(
            name,
            help=summary,
            description=description,
            epilog=f"examples:\n{lines}",
            formatter_class=_HelpFormatter,
            parents=[debug],
        )

    source_help = (
        "the stories file: Markdown (only the `## 4.` section if present) or stories v1 .json"
        " (env TESSELINT_SOURCE)"
    )

    p = command(
        "parse",
        "check that the source parses and count its stories",
        "Snapshot the source (Markdown: only the `## 4.` section if present), parse it and\n"
        "print its story and tombstone counts. Unparsed `- **US-` lines are listed with their\n"
        "line in the source, and exit 4; so does a Markdown file with no story at all.\n"
        "A .json source is validated as stories v1 first: one line per invalid field, exit 4;\n"
        "warnings print and exit 0.",
        "tesselint parse --source backlog.md",
        "tesselint parse --source backlog.md --epics E1,E2 --json",
        "tesselint parse --source stories.json",
    )
    p.add_argument(
        "--source",
        type=Path,
        default=env_source(),
        metavar="PATH",
        help=source_help,
    )
    p.add_argument(
        "--epics", type=_epics, metavar="E1,E2", help="count only these epics (default: every epic)"
    )
    p.add_argument("--json", action="store_true", help="print the counts as one JSON object")

    p = command(
        "run",
        "compare the stories with Jev and write proposals",
        "Snapshot and parse the source, estimate the cost, ask Jev about every pair and each\n"
        "story's atomicity, then write the matrix, clusters and proposals to a new run\n"
        "directory. Answers are cached by content, so a re-run pays only for edited stories.\n"
        "Progress goes to stderr; stdout gets one line: status, calls, cost, time, run dir.",
        "tesselint run --source backlog.md --dry-run",
        "tesselint run --source backlog.md --epics E1,E2",
        "tesselint run --resume runs/20260926-141200",
    )
    p.add_argument(
        "--source",
        type=Path,
        default=env_source(),
        metavar="PATH",
        help=source_help,
    )
    scope = p.add_mutually_exclusive_group()
    scope.add_argument(
        "--tier",
        choices=tuple(TIERS),
        help="presets for the calibration backlog; use --epics",
    )
    scope.add_argument(
        "--epics",
        type=_epics,
        metavar="E1,E2",
        help="only these epics, e.g. E1,E2 (all stories without either)",
    )
    p.add_argument(
        "--out", type=Path, default=DEFAULT_OUT_ROOT, metavar="DIR", help="where run dirs go"
    )
    p.add_argument(
        "--resume",
        type=Path,
        metavar="RUN_DIR",
        help="finish an interrupted run from its snapshot; pay only for missing answers",
    )
    p.add_argument(
        "--prefilter", action="store_true", help="ask Jev only about the local shortlist's pairs"
    )
    p.add_argument(
        "--prefilter-k", type=_positive_int, default=10, help="shortlist neighbours per story"
    )
    p.add_argument(
        "--no-swap", action="store_true", help="never re-ask overlapping pairs in swapped order"
    )
    p.add_argument(
        "--render", choices=("full", "core"), default="full", help="story text sent to Jev"
    )
    p.add_argument("--concurrency", type=_positive_int, default=16, help="Jev requests in flight")
    p.add_argument("--rate", type=_positive_float, default=50.0, help="Jev requests per second")
    p.add_argument(
        "--max-usd",
        type=_usd,
        default=env("TESSELINT_MAX_USD"),
        help="spend cap in US$ (env TESSELINT_MAX_USD); a larger estimate calls nothing",
    )
    p.add_argument("--refresh", action="store_true", help="ignore cached answers and ask again")
    p.add_argument(
        "--dry-run", action="store_true", help="estimate and write outputs, call nothing, no key"
    )
    p.add_argument(
        "--estimate-json",
        action="store_true",
        help="print the estimate as one JSON line (to_ask, cached, usd, cap_usd, over_cap,"
        " snapshot_sha256) and stop: no call, no key, no run dir; works with --resume",
    )
    p.add_argument(
        "--progress-jsonl",
        type=Path,
        metavar="PATH",
        help="append progress events (run_dir, estimate, phase, done) to PATH, one JSON per line",
    )

    p = command(
        "synth",
        "write a synthetic story set with planted answers",
        "Write stories.md, a Markdown backlog with planted duplicates, containments and\n"
        "bundles, and truth.json listing them. The same seed always writes the same bytes.\n"
        "--format json writes the same stories as stories.json (stories v1, x-synthetic).",
        "tesselint synth --out synth",
        "tesselint run --source synth/stories.md",
        "tesselint eval runs/RUN_ID --truth synth/truth.json",
        "tesselint synth --out synth --n 24 --format json",
    )
    p.add_argument("--out", type=Path, required=True, metavar="DIR", help="directory to write to")
    p.add_argument("--seed", type=int, default=42, help="random seed")
    p.add_argument("--n", type=_positive_int, default=60, help="live stories to write")
    p.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        help="md writes stories.md; json writes stories.json (stories v1, marked x-synthetic)",
    )

    p = command(
        "eval",
        "score a run against planted truth or hand labels",
        "Score a run directory. --truth compares its proposals with a synth truth.json.\n"
        "--make-labels writes a blind sheet of pairs to label; --labels scores Jev against\n"
        "the filled sheet and writes RUN_DIR/calibration.json.",
        "tesselint eval runs/RUN_ID --truth synth/truth.json",
        "tesselint eval runs/RUN_ID --make-labels labels.jsonl",
        "tesselint eval runs/RUN_ID --labels labels.jsonl",
    )
    p.add_argument("run_dir", type=Path, metavar="RUN_DIR", help="a directory made by `run`")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--truth", type=Path, metavar="FILE", help="synth truth.json to score")
    mode.add_argument("--labels", type=Path, metavar="FILE", help="filled label sheet to score")
    mode.add_argument("--make-labels", type=Path, metavar="FILE", help="write a blank label sheet")
    p.add_argument("--n", type=_positive_int, default=80, help="pairs on the label sheet")

    p = command(
        "report",
        "rebuild a run's proposals with new thresholds, offline",
        "Recompute the clusters and proposals of a run from its saved answers with new\n"
        "thresholds. No Jev call and no key.",
        "tesselint report runs/RUN_ID --merge 0.6 --split 0.85",
    )
    p.add_argument("run_dir", type=Path, metavar="RUN_DIR", help="a directory made by `run`")
    p.add_argument(
        "--merge", type=_probability, default=DEFAULT_THRESHOLDS.merge, help="merge at m >= this"
    )
    p.add_argument(
        "--cluster", type=_probability, default=DEFAULT_THRESHOLDS.cluster, help="cluster cut on o"
    )
    p.add_argument(
        "--split", type=_probability, default=DEFAULT_THRESHOLDS.split, help="split at atomic >="
    )
    p.add_argument(
        "--swap-gate",
        type=_probability,
        default=DEFAULT_THRESHOLDS.swap_gate,
        help="o from which a pair was re-asked in swapped order",
    )

    p = command(
        "serve",
        "open the local web UI to review proposals",
        "Serve the review UI on a loopback address: browse runs, decide on proposals, export\n"
        "the decisions and start estimated, capped re-runs. serve never reads .env, never\n"
        "calls Jev itself and never writes a source or run directory; decisions, jobs and\n"
        "exports live in the state dir. A non-loopback --host or a busy --port exits 2.",
        "tesselint serve",
        "tesselint serve --source backlog.md --ui-max-usd 0.2",
        "tesselint serve --runs tests/fixtures/ui/runs --state /tmp/state --port 8799 --no-open",
    )
    p.add_argument(
        "--host", default=DEFAULT_SERVE_HOST, help="loopback address to bind (127.0.0.1, ::1)"
    )
    p.add_argument("--port", type=_port, default=DEFAULT_SERVE_PORT, help="port to bind")
    p.add_argument(
        "--runs", type=Path, default=DEFAULT_OUT_ROOT, metavar="DIR", help="runs root to index"
    )
    p.add_argument(
        "--state",
        type=Path,
        default=Path(env("TESSELINT_STATE_DIR")),
        metavar="DIR",
        help="decisions, jobs and exports (env TESSELINT_STATE_DIR)",
    )
    p.add_argument(
        "--source",
        type=Path,
        action="append",
        metavar="PATH",
        help="a source the UI may re-run; repeat for more (TESSELINT_SOURCE and the runs'"
        " sources are allowed too)",
    )
    p.add_argument(
        "--ui-max-usd",
        type=_ui_usd,
        default=DEFAULT_UI_MAX_USD,
        help="most a re-run started from the UI may spend, in US$",
    )
    p.add_argument("--no-open", action="store_true", help="do not open a browser tab")

    p = command(
        "import",
        "convert a markdown backlog to stories v1 JSON",
        "Convert a Markdown backlog (only the `## 4.` section if present) to a stories v1 JSON\n"
        "document, word for word, so every story renders to the same Jev text and cache key.\n"
        "The document is validated before it is written: one line per invalid field, exit 4.",
        "tesselint import backlog.md --out stories.json",
        "tesselint parse --source stories.json",
    )
    p.add_argument("source", type=Path, metavar="SOURCE.md", help="the markdown backlog")
    p.add_argument("--out", type=Path, required=True, metavar="FILE", help="v1 JSON to write")

    p = command(
        "export",
        "print a stories v1 JSON file as markdown for editing",
        "Validate a stories v1 JSON file and print it on stdout as a Markdown backlog that\n"
        "`tesselint import` turns back into the same stories. Only US-<n>.<m> story IDs and\n"
        "E<n> epic IDs can be written this way.",
        "tesselint export stories.json > backlog.md",
        "tesselint import backlog.md --out stories.json",
    )
    p.add_argument("file", type=Path, metavar="FILE", help="a stories v1 JSON file")

    p = command(
        "key",
        "show whether the OpenRouter key is set and its balance",
        "Ask OpenRouter's free key endpoint (no Jev call, no cost) about OPENROUTER_API_KEY and\n"
        "print its usage, limit and remaining balance. The key itself is never printed.",
        "tesselint key",
        "tesselint key --json",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="print one JSON object: present, usage_usd, limit_usd, remaining_usd, error",
    )
    return parser


def JevClient(api_key: str, *, base_url: str, model: str, max_connections: int = 16) -> "Client":
    """A tesselint.jev.JevClient, importing jev (and httpx) only when a command calls Jev.

    It keeps the class's name so tests can replace it: `monkeypatch.setattr(cli, "JevClient", …)`.
    """
    from tesselint.jev import JevClient as client_class

    return client_class(api_key, base_url=base_url, model=model, max_connections=max_connections)


# python-dotenv, imported on first use; module-level names so tests can spy on them.


def find_dotenv(*, usecwd: bool = False) -> str:
    """dotenv.find_dotenv: the nearest .env, from the cwd when `usecwd`, else ""."""
    import dotenv

    return dotenv.find_dotenv(usecwd=usecwd)


def load_dotenv(path: str, *, override: bool = False) -> bool:
    """dotenv.load_dotenv: set the variables of the .env at `path` (never overriding by default)."""
    import dotenv

    return dotenv.load_dotenv(path, override=override)


def _is_jev_fatal(exc: BaseException) -> bool:
    """Whether `exc` is a JevFatal; jev is never imported for this (nothing raised one if not)."""
    jev = sys.modules.get("tesselint.jev")
    return jev is not None and isinstance(exc, jev.JevFatal)


class _Progress:
    """Prints pipeline progress to stderr; remembers the run dir its `run dir: …` line names."""

    def __init__(self) -> None:
        self.run_dir: str | None = None

    def __call__(self, line: str) -> None:
        if self.run_dir is None and line.startswith("run dir: "):
            self.run_dir = line.removeprefix("run dir: ")
        print(line, file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand and return its exit code; every failure is one line (DESIGN §7)."""
    progress = _Progress()
    args: argparse.Namespace | None = None
    words = list(sys.argv[1:] if argv is None else argv)
    try:
        # `serve` never reads .env: only CLI subprocesses may see the key (docs/ui/DESIGN.md §1).
        if next((w for w in words if not w.startswith("-")), None) != "serve":
            load_dotenv(find_dotenv(usecwd=True), override=False)
        args = build_parser().parse_args(words)
        match args.subcommand:
            case "parse":
                return _parse(args)
            case "run":
                return _run(args, progress)
            case "synth":
                return _synth(args)
            case "eval":
                return _eval(args)
            case "serve":
                return _serve(args)
            case "import":
                return _import(args)
            case "export":
                return _export(args)
            case "key":
                return _key(args)
            case _:
                return _report(args)
    except KeyboardInterrupt:
        run_dir = progress.run_dir
        hint = f"; resume with: tesselint run --resume {run_dir}" if run_dir else ""
        return _fail(f"interrupted{hint}", ExitCode.INTERRUPTED, args)
    except TesseLintError as exc:
        if isinstance(exc, inputv1.InputProblemsError):  # one line per problem, then the count
            for line in exc.lines:
                print(line, file=sys.stderr)
        return _fail(str(exc), exc.exit_code, args)
    except (OSError, ValueError, KeyError) as exc:  # json.JSONDecodeError is a ValueError
        return _fail(_describe(exc), ExitCode.BAD_INPUT, args)
    except Exception as exc:
        if _is_jev_fatal(exc):
            return _fail(f"Jev refused the request: {exc}", ExitCode.JEV_AUTH, args)
        return _fail(f"unexpected {type(exc).__name__}: {exc}", ExitCode.UNEXPECTED, args)


def _error(message: str) -> None:
    """Print `message` as one `tesselint: error:` line on stderr."""
    print(f"tesselint: error: {' '.join(message.split())}", file=sys.stderr, flush=True)


def _fail(message: str, code: int, args: argparse.Namespace | None) -> int:
    """Report the exception being handled (with its traceback under --debug); return `code`."""
    if getattr(args, "debug", False):
        traceback.print_exc()
    _error(message)
    return code


def _describe(exc: BaseException) -> str:
    """A one-line account of a bad-input exception."""
    if isinstance(exc, OSError) and exc.filename is not None:
        return f"{exc.strerror or type(exc).__name__}: {exc.filename}"
    if isinstance(exc, KeyError):
        return f"bad input: missing field {exc}"
    return f"bad input: {exc}"


def _parse(args: argparse.Namespace) -> int:
    """`tesselint parse`: counts, plus every unparsed line with its line in the source."""
    if args.source is None:
        raise UsageError(NO_SOURCE)
    source: Path = args.source
    if is_v1(source):
        return _parse_v1(args)
    raw = source.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = take_snapshot(source, Path(tmp))
    parsed = parse_section(snapshot.text)
    stories = select_epics(parsed.stories, args.epics)
    tombstones = [t for t in parsed.tombstones if _in_epics(t, args.epics)]
    start = raw.find(snapshot.text.encode("utf-8"))
    offset = raw[:start].count(b"\n") if start > 0 else 0  # parse numbers lines from `## 4.`
    unparsed = [{"line": line + offset, "text": text} for line, text in parsed.unparsed]
    if not parsed.stories and not unparsed:
        raise InputError(no_story_message(str(source)))

    if args.json:
        counts = {"stories": len(stories), "tombstones": len(tombstones), "unparsed": unparsed}
        print(json.dumps(counts))
    else:
        print(f"{len(stories)} stories, {len(tombstones)} tombstones")
    for row in unparsed:
        print(f"{source}:{row['line']}: unparsed: {row['text']}", file=sys.stderr)
    if unparsed:
        raise InputError(f"{len(unparsed)} unparsed story line(s) in {source}; fix them first")
    return ExitCode.OK


def _parse_v1(args: argparse.Namespace) -> int:
    """`tesselint parse --source F.json`: validate (INPUT.md §4), then count like Markdown."""
    source: Path = args.source
    doc = inputv1.load_v1(source)
    _warn(inputv1.check_v1(doc, str(source)), str(source))
    _print_counts(inputv1.to_parse_result(doc), args.epics, as_json=args.json)
    return ExitCode.OK


def _print_counts(parsed: ParseResult, epics: tuple[str, ...] | None, *, as_json: bool) -> None:
    """Story and tombstone counts of a v1 document, which has no unparsed lines."""
    stories = select_epics(parsed.stories, epics)
    tombstones = [t for t in parsed.tombstones if _in_epics(t, epics)]
    if as_json:
        print(json.dumps({"stories": len(stories), "tombstones": len(tombstones), "unparsed": []}))
    else:
        print(f"{len(stories)} stories, {len(tombstones)} tombstones")


def _warn(problems: Sequence[inputv1.InputProblem], label: str) -> None:
    """Print each validation warning as one stderr line naming `label` (INPUT.md §4)."""
    for problem in problems:
        print(problem.describe(label), file=sys.stderr)


def _in_epics(tombstone: Tombstone, epics: tuple[str, ...] | None) -> bool:
    """Whether a tombstone belongs to `epics`, by heading or ID, as `select_epics` decides."""
    id_epic = "E" + tombstone.id.removeprefix("US-").split(".")[0]
    return epics is None or tombstone.epic in epics or id_epic in epics


def _run(args: argparse.Namespace, progress: _Progress) -> int:
    """`tesselint run`: resolve the config, check the key, run, print one summary line.

    `--estimate-json` prints the estimate as one JSON line and stops (exit 0, over the cap or
    not); `--progress-jsonl PATH` appends the pipeline's events to PATH (docs/ui/DESIGN.md §4.4).
    """
    from tesselint import pipeline

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # before model2vec loads
    if args.source is None and args.resume is None:  # a resumed run reads its own snapshot
        raise UsageError(NO_SOURCE)
    cfg = RunConfig(
        source=args.source or Path(),
        out_root=args.out,
        epics=TIERS[args.tier] if args.tier else args.epics,
        render=args.render,
        prefilter=args.prefilter,
        prefilter_k=args.prefilter_k,
        swap=not args.no_swap,
        concurrency=args.concurrency,
        rate_per_s=args.rate,
        max_usd=args.max_usd,
        cache_dir=Path(env("TESSELINT_CACHE_DIR")),
        model=env("TESSELINT_MODEL"),
        base_url=env("OPENROUTER_BASE_URL"),
        resume=args.resume,
        refresh=args.refresh,
        dry_run=args.dry_run,
        thresholds=DEFAULT_THRESHOLDS,
        tier=args.tier,
    )
    if cfg.resume is None and not cfg.source.is_file():
        raise InputError(f"no such source file: {cfg.source}")
    if args.estimate_json:
        _say_tier_epics(args.tier)
        print(json.dumps(pipeline.estimate(cfg)), flush=True)
        return ExitCode.OK
    if cfg.resume is not None and cfg.dry_run and (cfg.resume / "run.json").is_file():
        raise UsageError(
            "--dry-run cannot be combined with --resume: it would overwrite the run's outputs"
        )
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key and not cfg.dry_run:
        _error("OPENROUTER_API_KEY is not set. Add it to .env or the environment.")
        return ExitCode.JEV_AUTH
    _say_tier_epics(args.tier)

    async def execute() -> Path:
        client = JevClient(
            api_key or "dry-run",
            base_url=cfg.base_url,
            model=cfg.model,
            max_connections=cfg.concurrency,
        )
        try:
            if args.progress_jsonl is None:
                return await pipeline.run(cfg, client, progress=progress)
            events = _EventLog(args.progress_jsonl)
            return await pipeline.run(cfg, client, progress=progress, events=events)
        finally:
            await client.aclose()

    run_dir = asyncio.run(execute())
    meta = json.loads((run_dir / "run.json").read_text("utf-8"))
    status, calls, cost = meta["status"], meta["calls"], meta["cost_usd"]
    estimate = f" · estimate ${cost['preflight_estimate']:.4f}" if cfg.dry_run else ""
    print(
        f"{status} · {calls['live']} live / {calls['cached']} cached calls"
        f" · ${cost['live']:.4f}{estimate} · {meta['wall_s']:.1f}s → {run_dir}",
        flush=True,
    )
    code = STATUS_EXIT.get(status, ExitCode.OK)
    resume = f"resume with: tesselint run --resume {run_dir}"
    if code == ExitCode.SPEND_CAP:
        _error(
            f"stopped at the spend cap of ${meta['max_usd']:g} (estimate"
            f" ${cost['preflight_estimate']:.4f}, spent ${cost['live']:.4f});"
            " raise --max-usd or TESSELINT_MAX_USD"
        )
    elif code == ExitCode.TOO_MANY_FAILURES:
        _error(f"stopped after too many failed Jev calls ({calls['failed']} failed); {resume}")
    elif code == ExitCode.INTERRUPTED:
        _error(f"interrupted; {resume}")
    return code


def _say_tier_epics(tier: str | None) -> None:
    """`epics: E1,E2,E20` on stderr: a --tier preset fits the calibration backlog only."""
    if tier:
        epics = TIERS[tier]
        print(f"epics: {','.join(epics) if epics else 'every epic'}", file=sys.stderr, flush=True)


class _EventLog:
    """`run --progress-jsonl PATH`: appends each progress event to PATH as one JSON line."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, event: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")


def _synth(args: argparse.Namespace) -> int:
    """`tesselint synth`: write stories.md (or stories.json, v1) and truth.json (DESIGN §6)."""
    out: Path = args.out
    if args.format == "json":
        doc, truth = generate_v1(seed=args.seed, n=args.n)
        stories_path = out / "stories.json"
        _warn(inputv1.check_v1(doc, str(stories_path)), str(stories_path))
        document = inputv1.dumps_v1(doc)
    else:
        document, truth = generate(seed=args.seed, n=args.n)
        stories_path = out / "stories.md"
    truth_json = truth_to_dict(truth)
    out.mkdir(parents=True, exist_ok=True)
    truth_path = out / "truth.json"
    stories_path.write_text(document, encoding="utf-8")
    truth_path.write_text(json.dumps(truth_json, indent=2), encoding="utf-8")
    print(
        f"wrote {stories_path} and {truth_path} · {truth.n} stories: "
        f"{len(truth.duplicates)} duplicates, {len(truth.containments)} containments, "
        f"{len(truth.bundled)} bundles"
    )
    return ExitCode.OK


def _eval(args: argparse.Namespace) -> int:
    """`tesselint eval`: exactly one of --make-labels, --truth, --labels (DESIGN §5, §6)."""
    run_dir = _require_run(args.run_dir)
    if args.make_labels:
        sheet = make_label_sheet(run_dir, args.make_labels, n=args.n)
        rows = sum(1 for line in sheet.read_text("utf-8").splitlines() if line.strip())
        print(f"wrote {sheet} · {rows} rows to label")
    elif args.truth:
        scores: object = score_synthetic(run_dir, _load_truth(args.truth))
        print(json.dumps(scores, indent=2, default=_as_json))
    else:
        scores = score_labels(run_dir, args.labels)
        calibration = run_dir / "calibration.json"
        calibration.write_text(json.dumps(scores, indent=2, default=_as_json) + "\n", "utf-8")
        print(json.dumps(scores, indent=2, default=_as_json))
        print(f"wrote {calibration}", file=sys.stderr)
    return ExitCode.OK


def _report(args: argparse.Namespace) -> int:
    """`tesselint report`: rebuild proposals offline with new thresholds."""
    run_dir = _require_run(args.run_dir)
    th = dataclasses.replace(
        DEFAULT_THRESHOLDS,
        merge=args.merge,
        cluster=args.cluster,
        split=args.split,
        swap_gate=args.swap_gate,
    )
    from tesselint import pipeline

    pipeline.report(run_dir, th)
    proposals = json.loads((run_dir / "proposals.json").read_text("utf-8"))["proposals"]
    kinds = Counter(proposal["kind"] for proposal in proposals)
    print(
        f"wrote {run_dir / 'proposals.md'} and {run_dir / 'proposals.json'} · "
        f"{kinds['merge']} merge, {kinds['overlap']} overlap, {kinds['split']} split"
    )
    return ExitCode.OK


def _serve(args: argparse.Namespace) -> int:
    """`tesselint serve`: the local web UI on a loopback address (docs/ui/DESIGN.md §4.1).

    The source allowlist is every --source, then TESSELINT_SOURCE (the runs' own sources are
    added by the server). Ctrl-C or SIGTERM stops it; a non-loopback --host, a busy --port or a
    state dir locked by a live serve exit 2.
    """
    import secrets

    from tesselint.server.app import ServeConfig, run_server, ui_cli

    env_src = env_source()
    sources = [*(args.source or ()), *((env_src,) if env_src else ())]
    cfg = ServeConfig(
        host=args.host,
        port=args.port,
        runs_root=args.runs,
        state_dir=args.state,
        sources=tuple(dict.fromkeys(sources)),
        cli=ui_cli(),
        ui_max_usd=args.ui_max_usd,
        token=secrets.token_urlsafe(32),
    )
    with suppress(KeyboardInterrupt):
        run_server(cfg, open_browser=not args.no_open)
    return ExitCode.OK


def _import(args: argparse.Namespace) -> int:
    """`tesselint import`: markdown backlog to validated stories v1 JSON (INPUT.md §4).

    The document is validated before it is written: an error writes nothing and exits 4.
    """
    out: Path = args.out
    doc = inputv1.import_markdown(args.source)
    removed = sum(s.get("status") == "removed" for s in doc["stories"])
    if removed == len(doc["stories"]):
        raise InputError(no_story_message(str(args.source)))
    _warn(inputv1.check_v1(doc, str(out)), str(out))
    out.write_text(inputv1.dumps_v1(doc), encoding="utf-8")
    print(
        f"wrote {out} · {len(doc['stories']) - removed} stories, {removed} tombstones,"
        f" {len(doc['epics'])} epics"
    )
    return ExitCode.OK


def _export(args: argparse.Namespace) -> int:
    """`tesselint export`: stories v1 JSON to markdown on stdout (INPUT.md §4)."""
    source: Path = args.file
    doc = inputv1.load_v1(source)
    _warn(inputv1.check_v1(doc, str(source)), str(source))
    markdown = inputv1.export_markdown(doc)
    sys.stdout.write(markdown if markdown.endswith("\n") else markdown + "\n")
    return ExitCode.OK


NO_KEY = "OPENROUTER_API_KEY is not set. Add it to .env or the environment."
NO_BALANCE = (
    "OpenRouter did not report the key's balance: the key was refused or the service is unreachable"
)


def _key(args: argparse.Namespace) -> int:
    """`tesselint key`: key presence and balance, never the key (docs/ui/DESIGN.md §4.4).

    `--json` prints one KeyStatus and exits 0 whatever it says; the plain form prints the
    balance, or an error line and exit 5 when the key is missing or refused.
    """
    status = _key_status()
    if args.json:
        print(json.dumps(status), flush=True)
        return ExitCode.OK
    if status["error"] is not None:
        _error(status["error"])
        return ExitCode.JEV_AUTH
    usage, limit, remaining = status["usage_usd"], status["limit_usd"], status["remaining_usd"]
    if limit is None or remaining is None:
        print(f"key present · US${usage:.2f} used · no limit")
    else:
        print(f"key present · US${usage:.2f} used of US${limit:.2f} · US${remaining:.2f} left")
    return ExitCode.OK


def _key_status() -> KeyStatus:
    """OPENROUTER_API_KEY's presence and balance from the free key endpoint; no Jev call."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return KeyStatus(
            present=False, usage_usd=None, limit_usd=None, remaining_usd=None, error=NO_KEY
        )

    async def fetch() -> "KeyInfo | None":
        base_url, model = env("OPENROUTER_BASE_URL"), env("TESSELINT_MODEL")
        client = JevClient(api_key, base_url=base_url, model=model)
        try:
            return await client.key_info()
        finally:
            await client.aclose()

    info = asyncio.run(fetch())
    if info is None:
        return KeyStatus(
            present=True, usage_usd=None, limit_usd=None, remaining_usd=None, error=NO_BALANCE
        )
    limit = info.limit_usd
    remaining = None if limit is None else limit - info.usage_usd
    return KeyStatus(
        present=True, usage_usd=info.usage_usd, limit_usd=limit, remaining_usd=remaining, error=None
    )


def _require_run(path: Path) -> Path:
    """`path` if it holds a run.json, else an InputError (exit 4)."""
    if not (path / "run.json").is_file():
        raise InputError(f"not a run directory (no run.json): {path}")
    return path


def _load_truth(path: Path) -> Truth:
    """Read a synth truth.json (DESIGN §6); a malformed one is an InputError."""
    return load_truth(path)


def _as_json(value: object) -> Any:
    """json.dumps fallback: dataclasses (evaluate's scores) become dicts."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")
