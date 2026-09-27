"""Read side of the API: run bundles, matrices, proposals and pairs (docs/ui/DESIGN.md §7.1).

Also the source allowlist (§4.1): the configured sources, then every run's `source.path`, named
by a stable `source_id`; and the "vs the current source" status of a run (§2 Overview).
"""

import hashlib
import json
import math
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, cast

from tesselint.derive import Derived, derive, read_jsonl
from tesselint.errors import InputError
from tesselint.inputv1 import InputProblemsError
from tesselint.models import ParseResult, ProposalKind, Relation, Snapshot, Story, Thresholds
from tesselint.parse import epic_names, is_v1, load_snapshot, parse_snapshot, select_epics
from tesselint.prefilter import rounded_cos
from tesselint.proposals import Proposal, hub_rows
from tesselint.render import render, text_hash
from tesselint.server.cases import build_cases, proposal_conflicts
from tesselint.server.errors import ApiError, ErrorCode
from tesselint.server.payloads import (
    ClusterRow,
    Edge,
    EpicRef,
    Hub,
    Kind,
    MatrixPacked,
    PairDetail,
    Probs,
    ProposalPayload,
    ProposalsResponse,
    RelationName,
    RunBundle,
    RunSource,
    RunSummary,
    SourceStatus,
    StoryRow,
    ThresholdsJson,
    TombstoneRow,
)
from tesselint.server.runindex import (
    REF_PATTERN,
    RunEntry,
    newest_first,
    number,
    previous,
    scan_runs,
    started,
)
from tesselint.verdicts import REL_ORDER, PairVerdict

CALIBRATION_FILE = "calibration.json"
STAMP_FILES = ("run.json", "stories.jsonl", "pairs.jsonl", "snapshot.json", "snapshot.md")
KINDS: tuple[Kind, ...] = ("merge", "overlap", "split")
SECTION_START = re.compile(rb"^## 4\..*$", re.MULTILINE)  # as parse.take_snapshot cuts §4
SECTION_END = re.compile(rb"^## 5\..*$", re.MULTILINE)
REL_NAMES: list[RelationName] = [r.value for r in REL_ORDER]

# --- sources --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceRead:
    """A source as a re-run would snapshot it: its hash, its stories, and what would stop it."""

    sha256: str | None
    parsed: ParseResult | None
    problems: tuple[str, ...]


def read_source(path: Path) -> SourceRead:
    """Snapshot `path` in memory (§4 of Markdown, a v1 file whole) and parse it; writes nothing.

    `problems` are the lines that would make `run` exit 4: unparsed story lines, v1 errors, a
    missing or unreadable file.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return SourceRead(None, None, (f"source not found: {path}",))
    except OSError as e:
        return SourceRead(None, None, (f"cannot read the source {path}: {e.strerror or e}",))
    data, offset = (raw, 0) if is_v1(path) else _section(raw)
    sha = hashlib.sha256(data).hexdigest()
    try:
        parsed = parse_snapshot(Snapshot(path=path, text=data.decode("utf-8"), sha256=sha))
    except UnicodeDecodeError:
        return SourceRead(sha, None, (f"{path} is not UTF-8 text",))
    except InputProblemsError as e:
        return SourceRead(sha, None, (*e.lines, str(e)))
    except (InputError, ValueError) as e:
        return SourceRead(sha, None, (str(e),))
    problems = tuple(
        f"line {n + offset}: unparsed story line: {line.strip()}" for n, line in parsed.unparsed
    )
    return SourceRead(sha, parsed, problems)


def _section(raw: bytes) -> tuple[bytes, int]:
    """§4 of a Markdown source (from `## 4.` up to `## 5.`) and the lines before it."""
    start_match = SECTION_START.search(raw)
    start = start_match.start() if start_match else 0
    end_match = SECTION_END.search(raw[start:])
    data = raw[start : start + end_match.start()] if end_match else raw[start:]
    return data, raw[:start].count(b"\n")


class SourceList:
    """The source allowlist (§4.1), append-only so a `source_id` never changes while serving."""

    def __init__(self, configured: Iterable[Path] = ()) -> None:
        self._lock = threading.Lock()
        self._paths: list[Path] = []
        self._ids: dict[str, int] = {}
        for path in configured:
            self.add(path)

    def add(self, path: Path) -> int:
        """The id of `path`, allowing it first if it is new (same file, same id)."""
        key = os.path.realpath(path)
        with self._lock:
            if key not in self._ids:
                self._ids[key] = len(self._paths)
                self._paths.append(path)
            return self._ids[key]

    def id_of(self, path: Path) -> int | None:
        with self._lock:
            return self._ids.get(os.path.realpath(path))

    def get(self, source_id: int) -> Path | None:
        with self._lock:
            return self._paths[source_id] if 0 <= source_id < len(self._paths) else None

    def paths(self) -> list[Path]:
        with self._lock:
            return list(self._paths)


def run_source(entry: RunEntry) -> Path | None:
    """The source a run was made from (run.json `source.path`; relative to the cwd), if known."""
    source = entry.meta.get("source")
    raw = source.get("path") if isinstance(source, Mapping) else None
    if not isinstance(raw, str) or not raw or "\0" in raw:
        return None
    return Path(raw).absolute()


# --- summaries ------------------------------------------------------------------------------------


def summarize(entry: RunEntry) -> RunSummary:
    """The RunSummary of an index entry, from its run.json only (§7.3)."""
    meta = entry.meta
    source, counts, cost = (_mapping(meta.get(k)) for k in ("source", "proposals", "cost_usd"))
    path, fmt = source.get("path"), source.get("format")
    cached = cost.get("cached_equivalent", cost.get("cached"))
    return RunSummary(
        ref=entry.ref,
        kind=entry.kind,
        kind_inferred=entry.kind_inferred,
        dry_run=entry.dry_run,
        status=_text(meta.get("status")) or "unknown",
        source=RunSource(
            path=path if isinstance(path, str) else None,
            format=fmt if fmt == "md" or fmt == "json" else None,
        ),
        tier=_text(meta.get("tier")),
        n_stories=int(number(meta.get("n_stories"))),
        finished_at=_text(meta.get("finished_at")),
        cost_live_usd=number(cost.get("live")),
        cost_cached_usd=number(cached),
        snapshot_sha256=_text(meta.get("snapshot_sha256")) or "",
        proposals={kind: int(number(counts.get(kind))) for kind in KINDS},
    )


def run_thresholds(entry: RunEntry) -> Thresholds:
    """The thresholds a run was derived with (run.json `thresholds`; unknown fields default)."""
    raw = entry.meta.get("thresholds")
    if not isinstance(raw, Mapping):
        return Thresholds()
    changes: dict[str, Any] = {}
    for f in fields(Thresholds):
        value = raw.get(f.name)
        if not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value):
            changes[f.name] = int(value) if f.type is int or f.type == "int" else float(value)
    return replace(Thresholds(), **changes)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


# --- the store ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Facts:
    """What a run's snapshot and pairs say besides the derivation: epics, tombstones, cosines."""

    epics: list[EpicRef] | None  # None: no readable snapshot
    tombstones: list[TombstoneRow]
    cos: Mapping[tuple[str, str], float]


class _Lru[K: Hashable, V]:
    """A small thread-safe LRU map."""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, capacity)
        self._lock = threading.Lock()
        self._items: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K, make: Callable[[], V]) -> V:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = make()  # outside the lock: a derive takes a fraction of a second
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return value


class RunStore:
    """Resolves refs only through the index; caches the `capacity` latest derived runs (§4.1)."""

    def __init__(self, runs_root: Path, capacity: int = 3, *, sources: Sequence[Path] = ()) -> None:
        """Serve runs under `runs_root`, keeping `capacity` derived runs in memory (§7.1).

        `sources` (additive) are the configured allowlisted sources; the runs' own sources are
        added as the index finds them.
        """
        self.runs_root = runs_root
        self.allowlist = SourceList(sources)
        self._derived: _Lru[tuple[str, Thresholds, tuple[Any, ...]], Derived] = _Lru(capacity)
        self._facts: _Lru[tuple[str, tuple[Any, ...]], _Facts] = _Lru(capacity)

    # index

    def entries(self, include_dry: bool = False) -> list[RunEntry]:
        """Indexed runs, newest first; dry runs only with `include_dry` (§2 Runs, §4.2)."""
        found = newest_first(scan_runs(self.runs_root))
        return found if include_dry else [e for e in found if not e.dry_run]

    def entry(self, ref: str) -> RunEntry:
        """The indexed run named `ref`; anything else is 404 `run_not_found` (§4.1, §4.2)."""
        return _find(scan_runs(self.runs_root), ref)

    def sources(self) -> list[Path]:
        """The allowlist by `source_id`: configured sources, then the runs' sources (§4.1)."""
        for entry in sorted(scan_runs(self.runs_root), key=lambda e: (started(e), e.ref)):
            path = run_source(entry)
            if path is not None:
                self.allowlist.add(path)
        return self.allowlist.paths()

    def source(self, source_id: int) -> Path | None:
        """An allowlisted source by id, else None."""
        paths = self.sources()
        return paths[source_id] if 0 <= source_id < len(paths) else None

    # derivations

    def thresholds(self, ref: str) -> Thresholds:
        """The run's own thresholds (additive)."""
        return run_thresholds(self.entry(ref))

    def derived(self, ref: str, th: Thresholds | None = None) -> Derived:
        """The run derived with `th` (default: its own), cached (additive; §4.3)."""
        entry = self.entry(ref)
        return self._derive(entry, th or run_thresholds(entry))

    def current_source(self, ref: str) -> ParseResult | None:
        """The run's source as it is now, parsed; None if unknown or unreadable (additive)."""
        path = run_source(self.entry(ref))
        return None if path is None else read_source(path).parsed

    # §7.2 read routes

    def bundle(self, ref: str) -> RunBundle:
        """`GET /api/runs/{ref}`; an unknown ref is 404 `run_not_found` (§7.2)."""
        index = scan_runs(self.runs_root)
        entry = _find(index, ref)
        th = run_thresholds(entry)
        derived = self._derive(entry, th)
        facts = self._run_facts(entry)
        before = previous(index, entry)
        calibration, calibration_from = _calibration(index, entry)
        return RunBundle(
            run=summarize(entry),
            thresholds=cast(ThresholdsJson, asdict(th)),
            epics=facts.epics if facts.epics is not None else _epics_of(derived.stories),
            stories=_story_rows(derived),
            tombstones=facts.tombstones,
            clusters=_cluster_rows(derived),
            previous=before.ref if before else None,
            calibration=calibration,
            calibration_from=calibration_from,
            source_status=self._source_status(entry, derived.stories),
        )

    def matrix(self, ref: str) -> MatrixPacked:
        """`GET /api/runs/{ref}/matrix`: packed upper-triangle o, m and relation (§7.3)."""
        derived = self.derived(ref)
        ids = [s.id for s in derived.stories]
        rel_index = {r: i for i, r in enumerate(REL_ORDER)}
        o: list[float | None] = []
        m: list[float | None] = []
        rel: list[int | None] = []
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                v = derived.verdicts.get((a, b))
                o.append(None if v is None else v.overlap)
                m.append(None if v is None else v.merge)
                rel.append(None if v is None else rel_index[v.relation])
        return MatrixPacked(ids=ids, o=o, m=m, rel=rel, rel_order=list(REL_NAMES))

    def proposals(self, ref: str, th: Thresholds) -> ProposalsResponse:
        """`GET /api/runs/{ref}/proposals`, re-derived in memory with `th`, never written (§4.3)."""
        derived = self.derived(ref, th)
        reviewed = [p for p in derived.proposals if p.kind is not ProposalKind.LEAVE]
        conflicts = proposal_conflicts(derived.proposals)
        order = {s.id: i for i, s in enumerate(derived.stories)}
        epics = {s.id: s.epic for s in derived.stories}
        return ProposalsResponse(
            thresholds=cast(ThresholdsJson, asdict(th)),
            proposals=[_proposal_payload(p, conflicts.get(p.key, [])) for p in reviewed],
            cases=build_cases(reviewed, order, epics=epics),
            hubs=cast(list[Hub], hub_rows(reviewed)),
        )

    def pair(self, ref: str, a: str, b: str) -> PairDetail:
        """`GET /api/runs/{ref}/pairs/{a}/{b}` (§7.2, §7.3), in document order."""
        entry = self.entry(ref)
        derived = self._derive(entry, run_thresholds(entry))
        order = {s.id: i for i, s in enumerate(derived.stories)}
        if a not in order or b not in order or a == b:
            raise ApiError(ErrorCode.NOT_FOUND, "no such pair of stories in this run")
        first, second = sorted((a, b), key=order.__getitem__)
        v = derived.verdicts.get((first, second))
        if v is None:
            raise ApiError(ErrorCode.NOT_FOUND, f"{first} and {second} were not compared")
        cos = self._run_facts(entry).cos
        return PairDetail(
            a=v.a,
            b=v.b,
            overlap=v.overlap,
            merge=v.merge,
            relation=v.relation.value,
            fwd=_probs(v.fwd),
            swap=None if v.swap is None else _probs(v.swap),
            cos=cos.get((v.a, v.b), cos.get((v.b, v.a))),
            proposals=[
                p.key
                for p in derived.proposals
                if p.kind is not ProposalKind.LEAVE and {v.a, v.b} <= set(p.stories)
            ],
        )

    # helpers

    def _derive(self, entry: RunEntry, th: Thresholds) -> Derived:
        def make() -> Derived:
            try:
                return derive(entry.path, th)
            except InputError as e:
                raise ApiError(ErrorCode.RUN_UNREADABLE, f"run {entry.ref}: {e}") from e

        return self._derived.get((entry.ref, th, _stamp(entry.path)), make)

    def _run_facts(self, entry: RunEntry) -> _Facts:
        return self._facts.get((entry.ref, _stamp(entry.path)), lambda: _read_facts(entry))

    def _source_status(self, entry: RunEntry, stories: Sequence[Story]) -> SourceStatus:
        """The run against its source as it is now (§2 Overview "What changed")."""
        status = SourceStatus(
            source_id=None, same_snapshot=False, edited=[], added=[], removed=[], problems=[]
        )
        path = run_source(entry)
        if path is None:
            return status
        self.sources()
        status["source_id"] = self.allowlist.id_of(path)
        read = read_source(path)
        status["problems"] = list(read.problems)
        status["same_snapshot"] = read.sha256 == entry.meta.get("snapshot_sha256")
        if read.parsed is None or status["same_snapshot"]:
            return status
        now = {s.id: render(s) for s in select_epics(read.parsed.stories, _run_epics(entry))}
        then = {s.id: render(s) for s in stories}
        status["edited"] = [sid for sid in then if sid in now and now[sid] != then[sid]]
        status["added"] = [sid for sid in now if sid not in then]
        status["removed"] = [sid for sid in then if sid not in now]
        return status


def _find(index: Iterable[RunEntry], ref: str) -> RunEntry:
    if isinstance(ref, str) and REF_PATTERN.fullmatch(ref):
        for entry in index:
            if entry.ref == ref:
                return entry
        raise ApiError(ErrorCode.RUN_NOT_FOUND, f"no run {ref} under the runs root")
    raise ApiError(ErrorCode.RUN_NOT_FOUND, "not a run ref")


def _stamp(run_dir: Path) -> tuple[Any, ...]:
    """What changes when a run's files change (a resumed run appends to pairs.jsonl)."""
    stamp: list[Any] = []
    for name in STAMP_FILES:
        try:
            st = (run_dir / name).stat()
        except OSError:
            stamp.append(None)
        else:
            stamp.append((st.st_mtime_ns, st.st_size))
    return tuple(stamp)


def _run_epics(entry: RunEntry) -> list[str] | None:
    epics = entry.meta.get("epics")
    return [e for e in epics if isinstance(e, str)] if isinstance(epics, list) else None


def _read_facts(entry: RunEntry) -> _Facts:
    wanted = _run_epics(entry)
    epics: list[EpicRef] | None = None
    tombstones: list[TombstoneRow] = []
    try:
        snapshot = load_snapshot(entry.path)
        names = epic_names(snapshot)
        parsed = parse_snapshot(snapshot)
    except (OSError, UnicodeDecodeError, ValueError, InputError):
        pass  # a legacy run without a readable snapshot: epics come from its stories
    else:
        keep = set(wanted) if wanted is not None else None
        epics = [EpicRef(id=i, name=n) for i, n in names if keep is None or i in keep]
        tombstones = [
            TombstoneRow(id=t.id, title=t.title, target=t.target)
            for t in parsed.tombstones
            if keep is None or t.epic in keep or _id_epic(t.id) in keep
        ]
    cos: dict[tuple[str, str], float] = {}
    try:
        for row in read_jsonl(entry.path / "pairs.jsonl"):
            value = row.get("prefilter_cos")
            if row.get("b") is not None and isinstance(value, int | float):
                cos[(str(row["a"]), str(row["b"]))] = rounded_cos(value)
    except (OSError, UnicodeDecodeError, ValueError, KeyError, AttributeError):
        cos = {}
    return _Facts(epics, tombstones, cos)


def _id_epic(story_id: str) -> str:
    return "E" + story_id.removeprefix("US-").split(".")[0]


def _epics_of(stories: Sequence[Story]) -> list[EpicRef]:
    return [EpicRef(id=e, name="") for e in dict.fromkeys(s.epic for s in stories)]


def _story_rows(derived: Derived) -> list[StoryRow]:
    rows: list[StoryRow] = []
    for i, s in enumerate(derived.stories):
        text = render(s, "full")  # decisions hash the full text too (§3)
        rows.append(
            StoryRow(
                id=s.id,
                epic=s.epic,
                title=s.title,
                tag=s.tag,
                words=len(text.split()),
                text_hash=text_hash(text),
                text=text,
                atomic=derived.atomic.get(s.id),
                order=i,
            )
        )
    return rows


def _cluster_rows(derived: Derived) -> list[ClusterRow]:
    rows: list[ClusterRow] = []
    for c in derived.clusters:
        members = list(c.members)
        edges = [
            v
            for i, a in enumerate(members)
            for b in members[i + 1 :]
            if (v := derived.verdicts.get((a, b)) or derived.verdicts.get((b, a))) is not None
        ]
        edges.sort(key=lambda v: -v.overlap)
        rows.append(
            ClusterRow(
                id=c.id,
                members=members,
                mean_overlap=c.mean_overlap,
                edges=[_edge(v) for v in edges],
            )
        )
    return rows


def _edge(v: PairVerdict) -> Edge:
    return Edge(a=v.a, b=v.b, overlap=v.overlap, merge=v.merge, relation=v.relation.value)


def _probs(probs: Mapping[Relation, float]) -> Probs:
    return {name: probs.get(r, 0.0) for name, r in zip(REL_NAMES, REL_ORDER, strict=True)}


def _proposal_payload(p: Proposal, conflicts: list[str]) -> ProposalPayload:
    payload = {
        "id": p.id,
        "key": p.key,
        "kind": p.kind.value,
        "target": p.target,
        "stories": list(p.stories),
        "decisive_value": p.decisive_value,
        "borderline": p.borderline,
        "conflicts": conflicts,
        "details": dict(p.details or {}),
    }
    return cast(ProposalPayload, payload)


def _calibration(
    index: Sequence[RunEntry], entry: RunEntry
) -> tuple[dict[str, float | bool] | None, str | None]:
    """The run's calibration.json, else the newest same-kind run's, cited by ref (§2 Trust)."""
    own = _read_calibration(entry.path)
    if own is not None:
        return own, None
    for other in newest_first(index):
        if other.ref != entry.ref and other.kind == entry.kind:
            found = _read_calibration(other.path)
            if found is not None:
                return found, other.ref
    return None, None


def _read_calibration(run_dir: Path) -> dict[str, float | bool] | None:
    try:
        raw = json.loads((run_dir / CALIBRATION_FILE).read_text("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {
        k: v
        for k, v in raw.items()
        if isinstance(v, bool) or (isinstance(v, int | float) and math.isfinite(v))
    }
