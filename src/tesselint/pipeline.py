"""End-to-end run and offline re-report (DESIGN §7 flow)."""

import asyncio
import contextlib
import dataclasses
import json
import tempfile
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from tesselint import __version__
from tesselint.cache import AnswerCache, _answer_from_row, cache_key
from tesselint.cluster import average_linkage
from tesselint.config import USD_PER_TOKEN, RunConfig
from tesselint.derive import Derived as Derived
from tesselint.derive import derive
from tesselint.errors import InputError
from tesselint.jev import Answer, JevClient
from tesselint.models import ParseResult, Snapshot, Story, Thresholds
from tesselint.outputs import write_outputs, write_proposals
from tesselint.parse import (
    is_v1,
    load_snapshot,
    no_story_message,
    parse_snapshot,
    select_epics,
    take_snapshot,
)
from tesselint.prefilter import keep_pairs, similarity
from tesselint.proposals import propose
from tesselint.protocol import (
    DoneEvent,
    EstimateEvent,
    EstimateLine,
    PhaseEvent,
    ProgressEvent,
    RunDirEvent,
)
from tesselint.questions import ATOMIC_V2, REL_V1
from tesselint.render import render, text_hash
from tesselint.verdicts import PairVerdict, build_verdicts, overlap_matrix
from tesselint.workers import Job, JobResult, SpendLedger, estimate_tokens, run_jobs

Progress = Callable[[str], None]
Events = Callable[[Mapping[str, Any]], None]  # docs/ui/DESIGN.md §7.4 progress events
Status = Literal["complete", "partial_cap", "partial_errors", "interrupted"]

PREFILTER_TOP_PCT = 2.0
SWAP_SHARE = 0.33  # share of forward pairs past the swap gate: 32.6% on T1' (DESIGN §2.3)
JEV_POSITIVE = 0.5  # o, m at which a pair is a Jev positive for pre-filter recall (DESIGN §2.2)
LOCAL_MODEL = "local"  # model of the verdict given locally to identical texts (DESIGN §2.1)
OVERLAP_KEYS = ("duplicate", "A_contains_B", "B_contains_A", "partial_overlap")
SNAPSHOT_FILES = ("snapshot.md", "snapshot.json", "snapshot.sha256")
DEFAULT_RENDER_PROFILE = "md-clean/1"  # a v1 document without `render` (INPUT.md §3)
SYNTH_CONVERTER = "tesselint synth"  # converter prefix of a synthetic v1 document (UI §4.2)
LEGACY_SYNTH_CONVERTER = "story-sim synth"  # the same prefix in documents from before the rename


# --- run ------------------------------------------------------------------------------------------


async def run(
    cfg: RunConfig,
    client: JevClient,
    *,
    progress: Progress | None = None,
    events: Events | None = None,
) -> Path:
    """Snapshot, parse, estimate, ask Jev, build verdicts and outputs; return the run dir (§7).

    `progress` gets `run dir: …`, the estimate line, then fwd, atomic and swap progress lines,
    each at most once a second. `events` gets the docs/ui/DESIGN.md §7.4 events: `run_dir`,
    `estimate`, a `phase` event with each progress line (live calls only) and `done` with the
    run.json status. Bad input (unparsed story lines, an invalid v1 document, a missing source,
    a --resume dir that is not an intact run) raises InputError before any call and any event.
    If the calls stop on Ctrl-C, JevFatal or an unexpected error, partial outputs are written
    (status interrupted or partial_errors, with `error` in run.json), `done` is sent and the
    original exception is re-raised.
    """
    emit: Progress = progress or (lambda _line: None)
    notify: Callable[[ProgressEvent], None] = events or (lambda _event: None)
    started_at = datetime.now(UTC)
    opened = _open_run(cfg, started_at)
    cfg, run_dir = opened.cfg, opened.run_dir
    emit(f"run dir: {run_dir}")
    notify(RunDirEvent(event="run_dir", run_dir=str(run_dir.absolute())))
    stories = select_epics(opened.parsed.stories, cfg.epics)
    plan = _plan(cfg, stories)
    meta = _run_meta(cfg, opened, started_at, plan)

    cache = AnswerCache(cfg.cache_dir / "answers.jsonl")
    try:
        if cache.bad_lines:
            emit(
                f"warning: skipped {cache.bad_lines} unreadable lines in {cache.file_path}; "
                "their answers will be asked again"
            )
        preflight = _estimate(cfg, plan, cache)
        emit(
            f"estimate: {preflight.jobs} jobs ({preflight.to_ask} to ask,"
            f" {preflight.cached} cached) · ~{_usd(preflight.usd)} · cap {_usd(cfg.max_usd)}"
        )
        notify(EstimateEvent(event="estimate", to_ask=preflight.to_ask, usd=preflight.usd))
        meta["estimate"] = asdict(preflight) | {"cached": preflight.cached}
        meta["cost_usd"] = {"preflight_estimate": preflight.usd}

        over_cap = preflight.usd > cfg.max_usd
        if cfg.dry_run or over_cap:
            status: Status = "partial_cap" if over_cap else "complete"
            _finish(run_dir, cfg, plan, meta, _Calls(), status=status, key_usage_delta=None)
            notify(DoneEvent(event="done", status=status))
            return run_dir

        ledger = SpendLedger(cfg.max_usd, USD_PER_TOKEN)
        calls = _Calls()
        usage_before = await client.key_usage_usd()
        try:
            status = await _ask(cfg, client, cache, ledger, plan, calls, emit, notify)
        except (KeyboardInterrupt, asyncio.CancelledError) as exc:
            calls.rejected_usd = ledger.rejected_usd
            _finish_aborted(run_dir, cfg, plan, meta, calls, exc, "interrupted", None, notify)
            raise
        except Exception as exc:
            calls.rejected_usd = ledger.rejected_usd
            delta = _delta(usage_before, await client.key_usage_usd())
            _finish_aborted(run_dir, cfg, plan, meta, calls, exc, "partial_errors", delta, notify)
            raise
        calls.rejected_usd = ledger.rejected_usd
        delta = _delta(usage_before, await client.key_usage_usd())
        _finish(run_dir, cfg, plan, meta, calls, status=status, key_usage_delta=delta)
        notify(DoneEvent(event="done", status=status))
        return run_dir
    finally:
        cache.close()


def estimate(cfg: RunConfig) -> EstimateLine:
    """`run --estimate-json`: the preflight estimate of `cfg`, writing nothing (UI DESIGN §4.4).

    No call, no key and no run directory: a new source is snapshotted into a temporary directory,
    a --resume dir is only read, and the answer cache is read without being created or repaired.
    Bad input raises InputError, as `run` would.
    """
    if cfg.resume is not None:
        opened = _open_resume(cfg, cfg.resume)
    else:
        with tempfile.TemporaryDirectory(prefix="tesselint-estimate-") as scratch:
            opened = _open_source(cfg, Path(scratch))
    cfg = opened.cfg
    plan = _plan(cfg, select_epics(opened.parsed.stories, cfg.epics))
    found = _estimate(cfg, plan, _CacheReader(cfg.cache_dir / "answers.jsonl"))
    return EstimateLine(
        to_ask=found.to_ask,
        cached=found.cached,
        usd=found.usd,
        cap_usd=cfg.max_usd,
        over_cap=found.usd > cfg.max_usd,
        snapshot_sha256=opened.snapshot.sha256,
    )


class _Cache(Protocol):
    def get(self, key: str) -> Answer | None: ...


class _CacheReader:
    """A read-only answers.jsonl for the estimate: never created, repaired or appended to.

    Lines are read as AnswerCache reads them, so the estimate counts exactly what a run would
    find cached; unreadable lines are skipped (the run itself reports them).
    """

    def __init__(self, path: Path) -> None:
        self._entries: dict[str, Answer] = {}
        lines = path.read_bytes().split(b"\n") if path.is_file() else []
        for raw_line in lines:
            try:
                row = json.loads(raw_line.decode("utf-8")) if raw_line.strip() else None
            except ValueError:  # JSONDecodeError, UnicodeDecodeError: a torn line
                continue
            loaded = _answer_from_row(row)
            if loaded is not None:
                self._entries[loaded[0]] = loaded[1]

    def get(self, key: str) -> Answer | None:
        return self._entries.get(key)


@dataclass(frozen=True, slots=True)
class _Opened:
    """A run dir with its snapshot and stories, and where they came from (UI DESIGN §4.2)."""

    cfg: RunConfig
    run_dir: Path
    snapshot: Snapshot
    parsed: ParseResult
    origin: Mapping[str, Any]  # run.json `source`, `kind`, `input` (absent keys: unknown)


def _open_run(cfg: RunConfig, started_at: datetime) -> _Opened:
    """A new run dir with a fresh snapshot, or the --resume dir with its own snapshot and settings.

    Raises InputError, leaving nothing behind, when the source is missing, has unparsed lines or
    is an invalid stories v1 document.
    """
    if cfg.resume is not None:
        return _open_resume(cfg, cfg.resume)
    if not cfg.source.is_file():
        raise InputError(f"source not found: {cfg.source}")
    run_dir = _new_run_dir(cfg.out_root, started_at.strftime("%Y%m%d-%H%M%S"))
    try:
        return _open_source(cfg, run_dir)
    except BaseException:
        _discard_run_dir(run_dir)
        raise


def _open_source(cfg: RunConfig, run_dir: Path) -> _Opened:
    """Snapshot `cfg.source` into `run_dir` and parse it; bad input raises InputError."""
    try:
        snapshot = take_snapshot(cfg.source, run_dir)
    except (OSError, ValueError) as e:
        raise InputError(f"cannot read the source {cfg.source}: {e}") from e
    parsed = parse_snapshot(snapshot)
    if parsed.unparsed:
        offset = _source_line_offset(cfg.source, snapshot)
        raise InputError(_unparsed_message(parsed, str(cfg.source), offset))
    if not parsed.stories and not is_v1(cfg.source):
        raise InputError(no_story_message(str(cfg.source)))
    return _Opened(cfg, run_dir, snapshot, parsed, _origin(cfg.source, snapshot))


def _origin(source: Path, snapshot: Snapshot) -> dict[str, Any]:
    """run.json `source`, `kind` and, for a v1 document, `input` (docs/ui/DESIGN.md §4.2).

    `kind` is synthetic when the document sets `x-synthetic`, its converter is `tesselint synth`,
    or a synth truth.json sits beside the source; else real.
    """
    doc = _v1_document(snapshot)
    synthetic = (source.parent / "truth.json").is_file()
    origin: dict[str, Any] = {
        "source": {"path": str(source.absolute()), "format": "md" if doc is None else "json"}
    }
    if doc is not None:
        raw_source = doc.get("source")
        converter = raw_source.get("converter") if isinstance(raw_source, Mapping) else None
        origin["input"] = {
            "format": doc.get("format"),
            "version": doc.get("version"),
            "converter": converter,
            "render": doc.get("render", DEFAULT_RENDER_PROFILE),
        }
        synthetic |= doc.get("x-synthetic") is True
        synthetic |= isinstance(converter, str) and converter.startswith(
            (SYNTH_CONVERTER, LEGACY_SYNTH_CONVERTER)
        )
    origin["kind"] = "synthetic" if synthetic else "real"
    return origin


def _v1_document(snapshot: Snapshot) -> Mapping[str, Any] | None:
    """The snapshot's stories v1 document, or None for a Markdown snapshot."""
    if snapshot.path.suffix != ".json":
        return None
    doc = json.loads(snapshot.text)  # parse_snapshot has validated it
    if not isinstance(doc, Mapping):
        raise InputError(f"{snapshot.path}: not a stories v1 document")
    return doc


def _open_resume(cfg: RunConfig, run_dir: Path) -> _Opened:
    """Check the run dir, reload its snapshot (sha verified) and its job-defining settings."""
    has_snapshot = any((run_dir / name).is_file() for name in SNAPSHOT_FILES[:2])
    if not (run_dir / "run.json").is_file() or not has_snapshot:
        raise InputError(
            f"--resume {run_dir}: not a run directory"
            " (needs run.json and snapshot.md or snapshot.json)"
        )
    try:
        prior = json.loads((run_dir / "run.json").read_text("utf-8"))
        snapshot = load_snapshot(run_dir)
    except (OSError, ValueError, IndexError) as e:
        raise InputError(f"--resume {run_dir}: {type(e).__name__}: {e}") from e
    if not isinstance(prior, dict):
        raise InputError(f"--resume {run_dir}: run.json is not an object")
    parsed = parse_snapshot(snapshot)
    if parsed.unparsed:
        raise InputError(_unparsed_message(parsed, str(snapshot.path), 0))
    raw_prefilter = prior.get("prefilter")
    prefilter: dict[str, Any] = raw_prefilter if isinstance(raw_prefilter, dict) else {}
    epics = prior.get("epics")
    resumed = dataclasses.replace(
        cfg,
        epics=tuple(epics) if epics else None,
        render=prior.get("render", cfg.render),
        swap=prior.get("swap", cfg.swap),
        prefilter=prefilter.get("enabled", cfg.prefilter),
        prefilter_k=prefilter.get("k", cfg.prefilter_k),
        tier=prior.get("tier", cfg.tier),
    )
    # The run keeps where it came from; a run from before these fields gets its snapshot's
    # format, and no `kind`, so the run index infers it (docs/ui/DESIGN.md §4.2).
    fmt = "md" if snapshot.path.suffix == ".md" else "json"
    origin = {k: prior[k] for k in ("source", "kind", "input") if prior.get(k) is not None}
    origin.setdefault("source", {"path": None, "format": fmt})
    return _Opened(resumed, run_dir, snapshot, parsed, origin)


def _new_run_dir(out_root: Path, run_id: str) -> Path:
    """Create `out_root/run_id`, or `run_id-2`, `-3`… if taken: an existing dir is never reused."""
    out_root.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        run_dir = out_root / (run_id if n == 1 else f"{run_id}-{n}")
        try:
            run_dir.mkdir()
        except FileExistsError:
            n += 1
            continue
        return run_dir


def _discard_run_dir(run_dir: Path) -> None:
    """Remove a run dir this run just created, holding at most its snapshot files."""
    for name in SNAPSHOT_FILES:
        (run_dir / name).unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        run_dir.rmdir()


def _source_line_offset(source: Path, snapshot: Snapshot) -> int:
    """Lines of the source before the snapshot (§4 starts at its `## 4.` heading); 0 for JSON."""
    raw = source.read_bytes()
    at = raw.find(snapshot.text.encode("utf-8"))
    return raw[:at].count(b"\n") if at > 0 else 0


def _unparsed_message(parsed: ParseResult, where: str, line_offset: int) -> str:
    shown = [f"  line {n + line_offset}: {text.strip()[:120]}" for n, text in parsed.unparsed[:10]]
    if len(parsed.unparsed) > 10:
        shown.append(f"  … and {len(parsed.unparsed) - 10} more")
    head = f"{len(parsed.unparsed)} story line(s) in {where} could not be parsed; fix them first:"
    return "\n".join([head, *shown])


# --- jobs and estimate ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Plan:
    """The run's stories and every job the run may issue, before any call."""

    stories: tuple[Story, ...]
    kept: set[tuple[int, int]]  # pre-filter pairs, as document indices i < j
    prefilter: dict[tuple[str, str], tuple[float, bool]]
    fwd: list[Job]
    atomic: list[Job]
    local: list[JobResult]  # identical texts: a local duplicate verdict, no call (DESIGN §2.1)


def _plan(cfg: RunConfig, stories: Sequence[Story]) -> _Plan:
    """Render texts, compute the pre-filter, build forward, atomic and local jobs (§2.1, §2.2)."""
    texts = {s.id: render(s, cfg.render) for s in stories}
    hashes = {sid: text_hash(text) for sid, text in texts.items()}
    sim = similarity([texts[s.id] for s in stories])
    kept = keep_pairs(sim, k=cfg.prefilter_k, top_pct=PREFILTER_TOP_PCT)

    items = [(s.id, texts[s.id], hashes[s.id]) for s in stories]  # (id, text, hash)
    prefilter: dict[tuple[str, str], tuple[float, bool]] = {}
    fwd: list[Job] = []
    local: list[JobResult] = []
    for i, first in enumerate(items):
        for j in range(i + 1, len(items)):
            second = items[j]
            key = (min(first[0], second[0]), max(first[0], second[0]))
            prefilter[key] = (float(sim[i, j]), (i, j) in kept)
            if first[1] == second[1]:
                local.append(_local_duplicate(cfg, first, second))
            elif not cfg.prefilter or (i, j) in kept:
                fwd.append(_pair_job(cfg, first, second))

    atomic = [
        Job(
            key=cache_key(ATOMIC_V2, cfg.model, texts[s.id], None),
            a=s.id,
            b=None,
            order="fwd",
            state=texts[s.id],
            question=ATOMIC_V2,
            est_tokens=estimate_tokens(len(texts[s.id].split()), None),
            text_hash_a=hashes[s.id],
            text_hash_b=None,
        )
        for s in stories
        if len(texts[s.id].split()) >= cfg.thresholds.min_words_atomic
    ]
    return _Plan(tuple(stories), kept, prefilter, fwd, atomic, local)


def _pair_job(cfg: RunConfig, x: tuple[str, str, str], y: tuple[str, str, str]) -> Job:
    """Forward job for (id, text, hash) x and y: the smaller text hash is sent as A (§2.1)."""
    (a, text_a, hash_a), (b, text_b, hash_b) = sorted((x, y), key=lambda item: item[2])
    return Job(
        key=cache_key(REL_V1, cfg.model, text_a, text_b),
        a=a,
        b=b,
        order="fwd",
        state={"A": text_a, "B": text_b},
        question=REL_V1,
        est_tokens=estimate_tokens(len(text_a.split()), len(text_b.split())),
        text_hash_a=hash_a,
        text_hash_b=hash_b,
    )


def _swap_job(cfg: RunConfig, job: Job) -> Job:
    """The forward job asked the other way round."""
    assert job.b is not None and job.text_hash_b is not None and not isinstance(job.state, str)
    text_a, text_b = job.state["B"], job.state["A"]
    return dataclasses.replace(
        job,
        key=cache_key(REL_V1, cfg.model, text_a, text_b),
        a=job.b,
        b=job.a,
        order="swap",
        state={"A": text_a, "B": text_b},
        text_hash_a=job.text_hash_b,
        text_hash_b=job.text_hash_a,
    )


def _local_duplicate(cfg: RunConfig, x: tuple[str, str, str], y: tuple[str, str, str]) -> JobResult:
    """A cached, free `duplicate = 1.0` answer for two stories with the same text (§2.1)."""
    (a, text, text_digest), (b, _, _) = x, y
    job = Job(
        key=cache_key(REL_V1, cfg.model, text, text),
        a=a,
        b=b,
        order="fwd",
        state={"A": text, "B": text},
        question=REL_V1,
        est_tokens=0,
        text_hash_a=text_digest,
        text_hash_b=text_digest,
    )
    answer = Answer(
        question_id=REL_V1.id,
        choice="duplicate",
        confidence=1.0,
        probabilities={"duplicate": 1.0},
        model=LOCAL_MODEL,
        input_tokens=0,
        cost_usd=0.0,
        gen_id=LOCAL_MODEL,
        latency_ms=0.0,
    )
    return JobResult(job=job, answer=answer, cached=True, attempts=0, error=None)


@dataclass(frozen=True, slots=True)
class _Estimate:
    """Preflight: forward and atomic jobs, the cache misses among them, and their cost (§2.7)."""

    jobs: int
    to_ask: int
    expected_swaps: int
    usd: float

    @property
    def cached(self) -> int:
        return self.jobs - self.to_ask


def _estimate(cfg: RunConfig, plan: _Plan, cache: _Cache) -> _Estimate:
    """Cost only the cache misses (each key once), plus the swaps the forward misses may add."""
    misses: dict[str, Job] = {}
    for job in [*plan.fwd, *plan.atomic]:
        if job.key not in misses and (cfg.refresh or cache.get(job.key) is None):
            misses[job.key] = job
    pair_misses = [job for job in misses.values() if job.b is not None]
    story_tokens = sum(job.est_tokens for job in misses.values() if job.b is None)
    swap_share = SWAP_SHARE if cfg.swap else 0.0
    pair_tokens = sum(job.est_tokens for job in pair_misses) * (1.0 + swap_share)
    return _Estimate(
        jobs=len(plan.fwd) + len(plan.atomic),
        to_ask=len(misses),
        expected_swaps=round(swap_share * len(pair_misses)),
        usd=(pair_tokens + story_tokens) * USD_PER_TOKEN,
    )


# --- calls ----------------------------------------------------------------------------------------


@dataclass
class _Calls:
    """What the call phases produced so far; it survives a phase that aborts."""

    results: list[JobResult] = field(default_factory=list)
    by_status: dict[str, int] = field(default_factory=dict)
    swap_jobs: int = 0
    wall_s: float = 0.0
    rejected_usd: float = 0.0


async def _ask(
    cfg: RunConfig,
    client: JevClient,
    cache: AnswerCache,
    ledger: SpendLedger,
    plan: _Plan,
    calls: _Calls,
    emit: Progress,
    notify: Callable[[ProgressEvent], None],
) -> Status:
    """Forward pairs and atomicity in one pass, then swaps of pairs past the gate (§2.3).

    Forward and atomic jobs share one run_jobs call, so the 2% failure abort is measured on
    nearly all of the run's jobs, not on a small phase (DESIGN §11).
    """

    async def phase(groups: dict[str, list[Job]]) -> Status:
        return await _run_phase(groups, cfg, client, cache, ledger, calls, emit, notify)

    status = await phase({"fwd": plan.fwd, "atomic": plan.atomic})
    if status == "complete" and cfg.swap:
        swaps = [_swap_job(cfg, r.job) for r in calls.results if _passes_swap_gate(r, cfg)]
        calls.swap_jobs = len(swaps)
        status = await phase({"swap": swaps})
    return status


def _passes_swap_gate(result: JobResult, cfg: RunConfig) -> bool:
    """A forward rel.v1 answer with o >= the swap gate, cached answers included (§2.3)."""
    if result.answer is None or result.job.order != "fwd" or result.job.question.id != REL_V1.id:
        return False
    o = sum(result.answer.probabilities.get(k, 0.0) for k in OVERLAP_KEYS)
    return o >= cfg.thresholds.swap_gate


async def _run_phase(
    groups: dict[str, list[Job]],
    cfg: RunConfig,
    client: JevClient,
    cache: AnswerCache,
    ledger: SpendLedger,
    calls: _Calls,
    emit: Progress,
    notify: Callable[[ProgressEvent], None],
) -> Status:
    """Ask the groups' jobs in one run_jobs call, each distinct cache key once.

    Jobs sharing a key reuse its answer as cached; if that call failed, they fail with the same
    error (0 attempts of their own). Each group (fwd, atomic, swap) has its own progress line.
    """
    unique: dict[str, Job] = {}
    group_of: dict[str, str] = {}
    for name, jobs in groups.items():
        for job in jobs:
            unique.setdefault(job.key, job)
            group_of.setdefault(job.key, name)
    copies = [job for jobs in groups.values() for job in jobs if unique[job.key] is not job]
    tickers = {
        name: _Ticker(
            name, sum(group_of[key] == name for key in unique), ledger, cfg.max_usd, emit, notify
        )
        for name in groups
    }
    outcomes: dict[str, JobResult] = {}

    def on_result(result: JobResult) -> None:
        calls.results.append(result)
        outcomes[result.job.key] = result
        tickers[group_of[result.job.key]].update(result)

    started = time.perf_counter()
    try:
        _, stats = await run_jobs(
            list(unique.values()),
            client,
            cache,
            ledger,
            concurrency=cfg.concurrency,
            rate_per_s=cfg.rate_per_s,
            on_result=on_result,
            refresh=cfg.refresh,
            status_counts=calls.by_status,
        )
    finally:
        calls.wall_s += time.perf_counter() - started
        calls.results.extend(
            dataclasses.replace(outcomes[job.key], job=job, cached=True, attempts=0)
            if outcomes[job.key].answer is not None
            else dataclasses.replace(outcomes[job.key], job=job, attempts=0)
            for job in copies
            if job.key in outcomes
        )
    for ticker in tickers.values():
        ticker.flush()
    return stats.stopped


class _Ticker:
    """Progress of one phase: a line and a `phase` event at most once a second, and at the end."""

    def __init__(
        self,
        name: str,
        total: int,
        ledger: SpendLedger,
        cap_usd: float,
        emit: Progress,
        notify: Callable[[ProgressEvent], None],
    ) -> None:
        self.name, self.total, self.ledger, self.cap_usd = name, total, ledger, cap_usd
        self.emit, self.notify = emit, notify
        self.done = self.cached = self.failed = 0
        self._last = float("-inf")

    def update(self, result: JobResult) -> None:
        self.done += 1
        if result.cached:
            self.cached += 1
        elif result.answer is None:
            self.failed += 1
        now = time.monotonic()
        if now - self._last >= 1.0:
            self._last = now
            self.flush()

    def flush(self) -> None:
        spent = self.ledger.spent_usd
        self.emit(
            f"{self.name} {self.done}/{self.total} · cached {self.cached} · failed {self.failed}"
            f" · {_usd(spent)}/{_usd(self.cap_usd)}"
        )
        self.notify(
            PhaseEvent(
                event="phase", phase=self.name, done=self.done, total=self.total, spent_usd=spent
            )
        )


def _usd(value: float) -> str:
    return f"${value:.2f}" if value >= 0.1 or value == 0 else f"${value:.4f}"


def _delta(before: float | None, after: float | None) -> float | None:
    return None if before is None or after is None else after - before


# --- outputs --------------------------------------------------------------------------------------


def _run_meta(cfg: RunConfig, opened: _Opened, started_at: datetime, plan: _Plan) -> dict[str, Any]:
    """run.json fields known before any call (DESIGN §9; `source`, `kind`, `dry_run`, `input`:
    docs/ui/DESIGN.md §4.2)."""
    n = len(plan.stories)
    return {
        "run_id": opened.run_dir.name,
        "started_at": started_at.isoformat(),
        "snapshot_sha256": opened.snapshot.sha256,
        **opened.origin,
        "dry_run": cfg.dry_run,
        "tier": cfg.tier,
        "epics": list(cfg.epics) if cfg.epics else None,
        "n_stories": n,
        "n_tombstones": len(opened.parsed.tombstones),
        "n_pairs": n * (n - 1) // 2,
        "render": cfg.render,
        "swap": cfg.swap,
        "concurrency": cfg.concurrency,
        "rate_per_s": cfg.rate_per_s,
        "max_usd": cfg.max_usd,
        "model_requested": cfg.model,
        "questions": {REL_V1.id: REL_V1.hash, ATOMIC_V2.id: ATOMIC_V2.hash},
        "prefilter": {
            "enabled": cfg.prefilter,
            "k": cfg.prefilter_k,
            "top_pct": PREFILTER_TOP_PCT,
            "kept": len(plan.kept),
        },
        "thresholds": asdict(cfg.thresholds),
        "versions": {"tesselint": __version__},
    }


def _finish_aborted(
    run_dir: Path,
    cfg: RunConfig,
    plan: _Plan,
    meta: dict[str, Any],
    calls: _Calls,
    exc: BaseException,
    status: Status,
    key_usage_delta: float | None,
    notify: Callable[[ProgressEvent], None],
) -> None:
    """Write partial outputs of an aborted run and send `done`; a failure is noted on `exc`.

    Neither a failed write nor a failed event hides `exc`, which the caller re-raises.
    """
    error = "interrupted" if status == "interrupted" else f"{type(exc).__name__}: {exc}"[:500]
    try:
        _finish(
            run_dir,
            cfg,
            plan,
            meta,
            calls,
            status=status,
            key_usage_delta=key_usage_delta,
            error=error,
        )
    except Exception as write_error:
        exc.add_note(f"tesselint: partial outputs were not written to {run_dir}: {write_error!r}")
        return
    try:
        notify(DoneEvent(event="done", status=status))
    except Exception as event_error:
        exc.add_note(f"tesselint: the done event was not sent: {event_error!r}")


def _finish(
    run_dir: Path,
    cfg: RunConfig,
    plan: _Plan,
    meta: dict[str, Any],
    calls: _Calls,
    *,
    status: Status,
    key_usage_delta: float | None,
    error: str | None = None,
) -> None:
    """Build verdicts, clusters and proposals from the answers so far and write every §9 file."""
    th = cfg.thresholds
    asked = calls.results
    results = [*plan.local, *asked]
    verdicts = build_verdicts(plan.stories, results)
    ids = [s.id for s in plan.stories]
    clusters = average_linkage(ids, overlap_matrix(ids, verdicts), threshold=th.cluster)
    atomic_yes = {
        r.job.a: r.answer.probabilities.get("yes")
        for r in results
        if r.job.question.id == ATOMIC_V2.id and r.answer is not None
    }
    atomic_map = {s.id: atomic_yes.get(s.id) for s in plan.stories}
    proposals = propose(plan.stories, verdicts, clusters, atomic_map, th, prefilter=plan.prefilter)

    live = [r.answer for r in asked if r.answer is not None and not r.cached]
    cached = [r.answer for r in asked if r.answer is not None and r.cached]
    tokens = [a.input_tokens for a in live]
    latencies = [a.latency_ms for a in live]
    models = sorted({r.answer.model for r in asked if r.answer is not None})
    finished_at = datetime.now(UTC)
    local_pairs = {frozenset((r.job.a, r.job.b)) for r in plan.local}
    out = dict(meta)
    out.update(
        {
            "finished_at": finished_at.isoformat(),
            "wall_s": (finished_at - datetime.fromisoformat(meta["started_at"])).total_seconds(),
            "status": status,
            "jobs": {"fwd": len(plan.fwd), "swap": calls.swap_jobs, "atomic": len(plan.atomic)},
            "calls": {
                "live": len(live),
                "cached": len(cached),
                "failed": sum(r.answer is None for r in asked),
                "retries": sum(max(0, r.attempts - 1) for r in asked),
                "by_status": dict(calls.by_status),
            },
            "tokens": {
                "total": sum(tokens),
                "mean": float(np.mean(tokens)) if tokens else 0.0,
                "p95": float(np.percentile(tokens, 95)) if tokens else 0.0,
            },
            "cost_usd": {
                **meta["cost_usd"],
                "live": sum(a.cost_usd for a in live),
                "rejected_attempts": calls.rejected_usd,
                "cached_equivalent": sum(a.cost_usd for a in cached),
                "key_usage_delta": key_usage_delta,
            },
            "latency_ms": {
                "p50": float(np.percentile(latencies, 50)) if latencies else 0.0,
                "p95": float(np.percentile(latencies, 95)) if latencies else 0.0,
            },
            "calls_per_s": len(live) / calls.wall_s if calls.wall_s > 0 else 0.0,
            "models_resolved": models,
            "mixed_models": len(models) > 1,
            "prefilter": {
                **meta["prefilter"],
                "recall_vs_jev_overlap": _recall(plan, verdicts, local_pairs, lambda v: v.overlap),
                "recall_vs_jev_merge": _recall(plan, verdicts, local_pairs, lambda v: v.merge),
            },
        }
    )
    if error is not None:
        out["error"] = error
    write_outputs(
        run_dir, plan.stories, results, verdicts, clusters, proposals, out, prefilter=plan.prefilter
    )


def _recall(
    plan: _Plan,
    verdicts: Mapping[tuple[str, str], PairVerdict],
    local_pairs: Collection[frozenset[str | None]],
    score: Callable[[PairVerdict], float],
) -> float | None:
    """Share of Jev positives (score >= 0.5) the pre-filter kept; None if there are none (§2.2)."""
    pos = {s.id: i for i, s in enumerate(plan.stories)}
    positives = [
        tuple(sorted((pos[v.a], pos[v.b])))
        for v in verdicts.values()
        if score(v) >= JEV_POSITIVE and frozenset((v.a, v.b)) not in local_pairs
    ]
    if not positives:
        return None
    return sum(pair in plan.kept for pair in positives) / len(positives)


# --- report ---------------------------------------------------------------------------------------


def report(run_dir: Path, th: Thresholds) -> None:
    """Rebuild verdicts, clusters and proposals of a run with new thresholds, no calls (§7).

    `derive` plus writing proposals.json and proposals.md (docs/ui/DESIGN.md §4.3). Only the
    run's own stories (its `epics` in run.json) are re-derived. A calibration.json in the run dir
    fills the proposals header. A directory that is not a run raises InputError.
    """
    derived = derive(run_dir, th)
    meta = json.loads((run_dir / "run.json").read_text("utf-8"))
    write_proposals(run_dir, derived.stories, derived.proposals, meta, th)
