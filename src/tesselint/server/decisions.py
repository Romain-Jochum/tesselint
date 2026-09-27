"""The decisions store, `<state>/decisions.json` (docs/ui/DESIGN.md §3, §7.4).

Decisions are keyed by `proposal_key` (never by run-local IDs such as M1) and keep, per story, the
text hash, text and title they were made on, plus the proposal's decisive value and the run's
snapshot hash. Status, orphaning, supersession and `applied` are
computed per run by `views` and never stored.

Writes are atomic (`.bak`, `.tmp`, fsync, `os.replace`, directory fsync); a file changed on disk is
re-read before use, so hand edits win; a file that does not parse as a decisions file blocks every
read and write with 500 `decisions_unreadable` and is never overwritten. `acquire_state_lock` is
the `.lock` a `serve` process holds on its state dir (§3 Locking).
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

from tesselint.models import ParseResult, ProposalKind, Story
from tesselint.render import render, text_hash
from tesselint.server.errors import ApiError, ErrorCode
from tesselint.server.payloads import (
    DecisionChange,
    DecisionsFile,
    DecisionView,
    HistoryBatch,
    HistoryChange,
    StoredDecision,
)

if TYPE_CHECKING:
    from tesselint.derive import Derived
    from tesselint.proposals import Proposal

DECISIONS_FORMAT: Final = "tesselint/decisions"
DECISIONS_VERSION: Final = 1
DECISIONS_FILE = "decisions.json"
LOCK_FILE = ".lock"  # O_EXCL, holds the serve pid (§3 Locking)
SIMILAR_JACCARD = 0.5  # an orphaned overlap decision hints at a proposal this similar (§3)

DECIDABLE_KINDS = frozenset({ProposalKind.MERGE, ProposalKind.OVERLAP, ProposalKind.SPLIT})
ACTIONS = frozenset({"accept", "reject", "defer"})
CLEAR = "clear"  # a change's action that resets the stored action to null
LOCK_YOUNG_S = 2.0  # a lock without a readable pid this young may be mid-write: treat it as held

_RECORD_TEXT_FIELDS = ("key", "note", "run", "decided_at", "snapshot_sha256")
_RECORD_MAP_FIELDS = ("text_hashes", "titles", "texts")


class DecisionStore:
    """Atomic, locked, history-keeping store; hand edits win; never overwrites a bad file (§3)."""

    def __init__(self, path: Path) -> None:
        """Use the decisions file at `path` (§3 Location); nothing is read or created yet."""
        self.path = path
        self._lock = threading.Lock()
        self._doc: DecisionsFile | None = None
        self._signature: tuple[int, int, int, int] | None = None

    # --- public API -------------------------------------------------------------------------------

    def read(self) -> DecisionsFile:
        """The file as stored; unreadable is 500 `decisions_unreadable` and blocks writes (§3)."""
        with self._lock:
            return copy.deepcopy(self._load())

    def apply(
        self,
        run_ref: str,
        derived: Derived,
        snapshot_sha256: str,
        changes: Sequence[DecisionChange],
    ) -> int:
        """Apply one batch checked against the run (422 `unknown_key`); return the new rev (§3).

        Every key must be a merge, overlap or split proposal key of `derived`; the server fills in
        kind, target, stories, texts, hashes, titles and the decisive value from the run. A key
        that appears twice is applied in order. An empty batch writes nothing.
        """
        proposals = {p.key: p for p in derived.proposals if p.kind in DECIDABLE_KINDS and p.key}
        for change in changes:
            _check_change(change)
        unknown = sorted({c["key"] for c in changes if c["key"] not in proposals})
        with self._lock:
            doc = self._load()
            if unknown:
                raise ApiError(
                    ErrorCode.UNKNOWN_KEY,
                    f"not a proposal key of run {run_ref}: {', '.join(unknown)}",
                )
            if not changes:
                return doc["rev"]
            stories = {s.id: s for s in derived.stories}
            now = _now()
            decisions = dict(doc["decisions"])
            before: dict[str, StoredDecision | None] = {}
            for change in changes:
                key = change["key"]
                prev = decisions.get(key)
                before.setdefault(key, prev)
                decisions[key] = _record(
                    proposals[key],
                    stories,
                    action=_next_action(change, prev),
                    note=change.get("note", prev["note"] if prev else ""),
                    run_ref=run_ref,
                    snapshot_sha256=snapshot_sha256,
                    now=now,
                )
            batch_changes = [
                HistoryChange(key=key, before=old, after=decisions[key])
                for key, old in before.items()
            ]
            return self._commit(doc, decisions, HistoryBatch(rev=0, at=now, changes=batch_changes))

    def undo(self) -> int:
        """Append the inverse of the last batch with `undo_of`; return the new rev (§3 History).

        The last batch is the newest one that is neither an undo nor already undone; nothing left
        is 409 `nothing_to_undo`. Each inverse change starts from the record as it is now.
        """
        with self._lock:
            doc = self._load()
            target = _last_undoable(doc["history"])
            if target is None:
                raise ApiError(ErrorCode.NOTHING_TO_UNDO, "there is no decision batch to undo")
            decisions = dict(doc["decisions"])
            inverse: list[HistoryChange] = []
            for change in target["changes"]:
                key, restored = change["key"], change["before"]
                inverse.append(HistoryChange(key=key, before=decisions.get(key), after=restored))
                if restored is None:
                    decisions.pop(key, None)
                else:
                    decisions[key] = restored
            batch = HistoryBatch(rev=0, at=_now(), undo_of=target["rev"], changes=inverse)
            return self._commit(doc, decisions, batch)

    def views(self, derived: Derived, current_source: ParseResult | None) -> list[DecisionView]:
        """Each decision with status, proposed, superseded_by and applied for a run (§3).

        `status` compares the stored text hashes with the run's stories: `missing` if a story is
        gone, else `stale` if a hash differs, else `current`; `changed` names the stories that
        are gone or differ, and `old_texts` holds their stored texts. `applied` (merges only) is
        None without a current source. Views also carry the stored `decisive_value` (PLAN
        resolution 2), which the export needs.
        """
        with self._lock:
            doc = self._load()
        hashes = {s.id: text_hash(render(s, "full")) for s in derived.stories}
        proposed = {p.key for p in derived.proposals}
        tombstones = (
            {t.id: t.target for t in current_source.tombstones}
            if current_source is not None
            else None
        )
        decisions = doc["decisions"]
        accepted_by_drop = _accepted_merges_by_drop(decisions.values())
        return [
            _view(record, hashes, proposed, tombstones, accepted_by_drop)
            for record in decisions.values()
        ]

    # --- file handling ----------------------------------------------------------------------------

    def _load(self) -> DecisionsFile:
        """The current document, re-read when the file changed on disk (§3 Writes).

        A missing file is an empty store (rev 0) and is not created. Call with `_lock` held.
        """
        try:
            with self.path.open("rb") as fh:
                stat = os.fstat(fh.fileno())
                signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                if self._doc is not None and signature == self._signature:
                    return self._doc
                raw = fh.read()
        except FileNotFoundError:
            self._doc, self._signature = None, None
            return _empty()
        self._doc, self._signature = None, None
        doc = _parse(raw, self.path)
        self._doc, self._signature = doc, signature
        return doc

    def _commit(
        self, doc: DecisionsFile, decisions: dict[str, StoredDecision], batch: HistoryBatch
    ) -> int:
        """Write `decisions` with `batch` appended as rev + 1; memory changes only on success."""
        rev = doc["rev"] + 1
        batch["rev"] = rev
        new = DecisionsFile(
            format=DECISIONS_FORMAT,
            version=DECISIONS_VERSION,
            rev=rev,
            decisions=decisions,
            history=[*doc["history"], batch],
        )
        self._write(new)
        return rev

    def _write(self, doc: DecisionsFile) -> None:
        """`.bak` of the current file, then `.tmp`, fsync, replace, fsync the directory."""
        data = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copyfile(self.path, self.path.with_name(self.path.name + ".bak"))
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self.path)
        except BaseException:
            with suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(self.path.parent)
        stat = self.path.stat()
        self._doc = doc
        self._signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


# --- records and views ----------------------------------------------------------------------------


def _record(
    proposal: Proposal,
    stories: Mapping[str, Story],
    *,
    action: str | None,
    note: str,
    run_ref: str,
    snapshot_sha256: str,
    now: str,
) -> StoredDecision:
    """The §3 record of `proposal` on this run, texts and hashes from its stories."""
    ids = list(proposal.stories)
    texts = {sid: render(stories[sid], "full") for sid in ids}
    return cast(
        StoredDecision,
        {
            "key": proposal.key,
            "kind": proposal.kind.value,
            "target": proposal.target,
            "stories": ids,
            "action": action,
            "note": note,
            "text_hashes": {sid: text_hash(text) for sid, text in texts.items()},
            "titles": {sid: stories[sid].title for sid in ids},
            "run": run_ref,
            "decided_at": now,
            "texts": texts,
            "decisive_value": proposal.decisive_value,
            "snapshot_sha256": snapshot_sha256,
        },
    )


def _next_action(change: DecisionChange, previous: StoredDecision | None) -> str | None:
    """The change's action (`clear` is None); without one, the stored action stays."""
    if "action" not in change:
        return previous["action"] if previous else None
    action = change["action"]
    return None if action == CLEAR else action


def _view(
    record: StoredDecision,
    hashes: Mapping[str, str],
    proposed: set[str],
    tombstones: Mapping[str, str | None] | None,
    accepted_by_drop: Mapping[str, list[str]],
) -> DecisionView:
    ids = record["stories"]
    gone = [sid for sid in ids if sid not in hashes]
    changed = [
        sid for sid in ids if sid not in hashes or hashes[sid] != record["text_hashes"].get(sid)
    ]
    status = "missing" if gone else "stale" if changed else "current"
    view: dict[str, Any] = {
        "key": record["key"],
        "kind": record["kind"],
        "target": record["target"],
        "stories": list(ids),
        "action": record["action"],
        "note": record["note"],
        "text_hashes": dict(record["text_hashes"]),
        "titles": dict(record["titles"]),
        "decisive_value": record["decisive_value"],
        "run": record["run"],
        "decided_at": record["decided_at"],
        "status": status,
        "changed": changed,
        "proposed": record["key"] in proposed,
        "superseded_by": _superseded_by(record, accepted_by_drop),
        "applied": _applied(record, tombstones),
    }
    old_texts = {sid: record["texts"][sid] for sid in changed if sid in record["texts"]}
    if old_texts:
        view["old_texts"] = old_texts
    return cast(DecisionView, view)


def _accepted_merges_by_drop(records: Iterable[StoredDecision]) -> dict[str, list[str]]:
    """Accepted merge keys by dropped story, in file order."""
    found: dict[str, list[str]] = {}
    for record in records:
        if record["kind"] == "merge" and record["action"] == "accept":
            found.setdefault(record["stories"][1], []).append(record["key"])
    return found


def _superseded_by(record: StoredDecision, accepted_by_drop: Mapping[str, list[str]]) -> str | None:
    """A merge not accepted whose dropped story another accepted merge takes (§2 Review)."""
    if record["kind"] != "merge" or record["action"] == "accept":
        return None
    others = [k for k in accepted_by_drop.get(record["stories"][1], []) if k != record["key"]]
    return others[0] if others else None


def _applied(record: StoredDecision, tombstones: Mapping[str, str | None] | None) -> bool | None:
    """Merges only: the source holds `drop — merged into keep`; None when no source is known."""
    if record["kind"] != "merge" or tombstones is None:
        return None
    keep, drop = record["stories"]
    return drop in tombstones and tombstones[drop] == keep


def _last_undoable(history: Sequence[HistoryBatch]) -> HistoryBatch | None:
    """The newest batch that is not an undo and was not undone."""
    undone = {batch["undo_of"] for batch in history if "undo_of" in batch}
    for batch in reversed(history):
        if "undo_of" not in batch and batch["rev"] not in undone:
            return batch
    return None


# --- validation -----------------------------------------------------------------------------------


def _check_change(change: DecisionChange) -> None:
    """A malformed change is a ValueError; the API validates request bodies before this."""
    if not isinstance(change.get("key"), str):
        raise ValueError(f"a decision change needs a string key: {change!r}")
    action = change.get("action", CLEAR)
    if action != CLEAR and action not in ACTIONS:
        raise ValueError(f"unknown decision action {action!r} for {change['key']}")
    if not isinstance(change.get("note", ""), str):
        raise ValueError(f"a decision note must be a string ({change['key']})")


def _unreadable(path: Path, why: str) -> ApiError:
    return ApiError(
        ErrorCode.DECISIONS_UNREADABLE,
        f"{path} is unreadable ({why}); fix or move it by hand, it is never overwritten",
    )


def _parse(raw: bytes, path: Path) -> DecisionsFile:
    """The document in `raw`, or 500 `decisions_unreadable` naming the first problem."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise _unreadable(path, f"not UTF-8 JSON: {e}") from None
    problem = _document_problem(data)
    if problem is not None:
        raise _unreadable(path, problem)
    return cast(DecisionsFile, data)


def _document_problem(data: Any) -> str | None:
    if not isinstance(data, dict):
        return "not a JSON object"
    if data.get("format") != DECISIONS_FORMAT:
        return f"format is not {DECISIONS_FORMAT!r}"
    if not _is_int(data.get("version")) or data["version"] != DECISIONS_VERSION:
        return f"version is not {DECISIONS_VERSION}"
    if not _is_int(data.get("rev")) or data["rev"] < 0:
        return "rev is not a whole number >= 0"
    decisions, history = data.get("decisions"), data.get("history")
    if not isinstance(decisions, dict):
        return "decisions is not an object"
    if not isinstance(history, list):
        return "history is not a list"
    for key, record in decisions.items():
        problem = _record_problem(record)
        if problem is None and record["key"] != key:
            problem = "its key field differs"
        if problem is not None:
            return f"decision {key!r}: {problem}"
    for i, batch in enumerate(history):
        problem = _batch_problem(batch)
        if problem is not None:
            return f"history[{i}]: {problem}"
    return None


def _batch_problem(batch: Any) -> str | None:
    if not isinstance(batch, dict):
        return "not an object"
    if not _is_int(batch.get("rev")) or not isinstance(batch.get("at"), str):
        return "needs an integer rev and a string at"
    if "undo_of" in batch and not _is_int(batch["undo_of"]):
        return "undo_of is not an integer"
    changes = batch.get("changes")
    if not isinstance(changes, list):
        return "changes is not a list"
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("key"), str):
            return "a change without a string key"
        for side in ("before", "after"):
            if side not in change:
                return f"change {change['key']!r} lacks {side}"
            record = change[side]
            problem = None if record is None else _record_problem(record)
            if problem is not None:
                return f"change {change['key']!r} {side}: {problem}"
    return None


def _record_problem(record: Any) -> str | None:
    if not isinstance(record, dict):
        return "not an object"
    for name in _RECORD_TEXT_FIELDS:
        if not isinstance(record.get(name), str):
            return f"{name} is not a string"
    if record.get("kind") not in ("merge", "overlap", "split"):
        return "kind is not merge, overlap or split"
    stories = record.get("stories")
    if not isinstance(stories, list) or not stories or not all(isinstance(s, str) for s in stories):
        return "stories is not a non-empty list of IDs"
    if record["kind"] == "merge" and len(stories) != 2:
        return "a merge names two stories"
    if record.get("target") is not None and not isinstance(record["target"], str):
        return "target is not a string or null"
    if record.get("action") is not None and record["action"] not in ACTIONS:
        return "action is not accept, reject, defer or null"
    for name in _RECORD_MAP_FIELDS:
        value = record.get(name)
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            return f"{name} is not an object of strings"
    value = record.get("decisive_value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "decisive_value is not a number"
    return None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# --- helpers --------------------------------------------------------------------------------------


def _empty() -> DecisionsFile:
    return DecisionsFile(
        format=DECISIONS_FORMAT, version=DECISIONS_VERSION, rev=0, decisions={}, history=[]
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fsync_dir(directory: Path) -> None:
    """Make a rename in `directory` durable."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# --- the state-dir lock (§3 Locking) --------------------------------------------------------------


class StateLocked(Exception):
    """Another live process holds `<state>/.lock`; `serve` exits 2 on this."""

    def __init__(self, path: Path, pid: int | None) -> None:
        holder = f"process {pid}" if pid is not None else "another process"
        super().__init__(f"{path} is held by {holder}; is another tesselint serve running?")
        self.path = path
        self.pid = pid


def acquire_state_lock(state_dir: Path) -> Path:
    """Create `<state>/.lock` (O_EXCL) holding this pid; take it over if its pid is dead.

    Raises StateLocked while the holder lives (a lock with no readable pid counts as live for
    LOCK_YOUNG_S, as its writer may not have written it yet). Returns the lock path.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / LOCK_FILE
    for _ in range(3):
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            holder = _lock_pid(lock)
            if holder is not None and pid_alive(holder):
                raise StateLocked(lock, holder) from None
            if holder is None and _age_s(lock) < LOCK_YOUNG_S:
                raise StateLocked(lock, None) from None
            with suppress(FileNotFoundError):
                lock.unlink()
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n")
        return lock
    raise StateLocked(lock, _lock_pid(lock))


def release_state_lock(state_dir: Path) -> None:
    """Remove `<state>/.lock` if this process holds it."""
    lock = state_dir / LOCK_FILE
    if _lock_pid(lock) == os.getpid():
        with suppress(FileNotFoundError):
            lock.unlink()


def pid_alive(pid: int) -> bool:
    """Whether a process `pid` exists (a zombie counts; another user's process counts)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_pid(lock: Path) -> int | None:
    try:
        text = lock.read_text("utf-8").strip()
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() else None


def _age_s(path: Path) -> float:
    try:
        return time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return float("inf")
