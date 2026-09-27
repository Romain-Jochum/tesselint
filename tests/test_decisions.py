"""The decisions store, `<state>/decisions.json` (docs/ui/DESIGN.md §3, §7.4).

Planted runs: run 1 proposes two merges that drop US-1.2 (one group), a split of US-2.1 and an
overlap of US-1.3 and US-2.2. In run 2, US-1.1 is edited, US-2.1 is gone and the overlap is no
longer proposed.
"""

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tesselint.derive import Derived
from tesselint.models import ParseResult, ProposalKind, Story, Thresholds, Tombstone
from tesselint.proposals import Proposal
from tesselint.render import render, text_hash
from tesselint.server.decisions import DECISIONS_FORMAT, LOCK_FILE, DecisionStore
from tesselint.server.errors import ApiError, ErrorCode
from tesselint.server.payloads import DecisionChange, DecisionView
from tests.test_static_serve import serve, serve_argv, serve_env

RUN1 = "mini~20260101-090000"
RUN2 = "mini~20260102-090000"
SNAP1 = "1" * 64
MERGE_A = "merge:US-1.1>US-1.2"
MERGE_B = "merge:US-1.3>US-1.2"
SPLIT = "split:US-2.1"
OVERLAP = "overlap:US-1.3+US-2.2"
RUN_LOCAL_IDS = {"M1", "M2", "S1", "O1"}


def story(sid: str, title: str, core: str, subs: tuple[str, ...] = ()) -> Story:
    return Story(id=sid, epic="E" + sid[3], title=title, tag="", core=core, subs=subs, line=0)


S11 = story("US-1.1", "Profile page", "As a member I can view my profile with my photo and bio.")
S11_EDITED = story(
    "US-1.1", "Profile page", "As a member I can view and share my profile with photo and bio."
)
S12 = story("US-1.2", "Profile edits", "As a member I can edit my photo and bio on my profile.")
S13 = story("US-1.3", "Account settings", "As a member I can change my settings and profile.")
S21 = story(
    "US-2.1",
    "Search and alerts",
    "As a buyer I can search listings by price and area.",
    ("Save a search", "Get an email alert for new matches"),
)
S22 = story("US-2.2", "Saved searches", "As a buyer I can save a search and get alerts.")


def proposal(
    pid: str,
    kind: ProposalKind,
    target: str | None,
    stories: tuple[str, ...],
    value: float,
    key: str,
) -> Proposal:
    return Proposal(
        id=pid,
        kind=kind,
        target=target,
        stories=stories,
        decisive_value=value,
        borderline=False,
        evidence=(),
        key=key,
    )


P_MERGE_A = proposal("M1", ProposalKind.MERGE, "US-1.1", ("US-1.1", "US-1.2"), 0.91, MERGE_A)
P_MERGE_B = proposal("M2", ProposalKind.MERGE, "US-1.3", ("US-1.3", "US-1.2"), 0.62, MERGE_B)
P_SPLIT = proposal("S1", ProposalKind.SPLIT, None, ("US-2.1",), 0.86, SPLIT)
P_OVERLAP = proposal("O1", ProposalKind.OVERLAP, "C1", ("US-1.3", "US-2.2"), 0.74, OVERLAP)


def derived(stories: Sequence[Story], proposals: Sequence[Proposal]) -> Derived:
    return Derived(
        stories=tuple(stories),
        verdicts={},
        clusters=(),
        atomic={},
        proposals=tuple(proposals),
        thresholds=Thresholds(),
    )


RUN1_DERIVED = derived((S11, S12, S13, S21, S22), (P_MERGE_A, P_MERGE_B, P_SPLIT, P_OVERLAP))
RUN2_DERIVED = derived((S11_EDITED, S12, S13, S22), (P_MERGE_A, P_MERGE_B))


def change(key: str, action: str | None = None, note: str | None = None) -> DecisionChange:
    item: dict[str, Any] = {"key": key}
    if action is not None:
        item["action"] = action
    if note is not None:
        item["note"] = note
    return item  # type: ignore[return-value]


ALL_FOUR = [
    change(MERGE_A, "accept", "same page"),
    change(MERGE_B, "defer"),
    change(SPLIT, "accept"),
    change(OVERLAP, "reject"),
]


@pytest.fixture
def path(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    return state / "decisions.json"


@pytest.fixture
def store(path: Path) -> DecisionStore:
    return DecisionStore(path)


def views_by_key(views: Sequence[DecisionView]) -> dict[str, DecisionView]:
    return {view["key"]: view for view in views}


# --- apply, history, undo -------------------------------------------------------------------------


def test_apply_stores_the_full_record(store: DecisionStore) -> None:
    """§3 Record: kind, target, stories, action, note, hashes, texts, titles, value, run, snap."""
    rev = store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "accept", "same page")])

    doc = store.read()
    assert rev == doc["rev"] == 1
    assert (doc["format"], doc["version"]) == (DECISIONS_FORMAT, 1)
    record = doc["decisions"][MERGE_A]
    assert record["kind"] == "merge"
    assert record["target"] == "US-1.1"
    assert record["stories"] == ["US-1.1", "US-1.2"]
    assert (record["action"], record["note"]) == ("accept", "same page")
    assert record["texts"] == {s.id: render(s, "full") for s in (S11, S12)}
    assert record["text_hashes"] == {s.id: text_hash(render(s, "full")) for s in (S11, S12)}
    assert record["titles"] == {"US-1.1": "Profile page", "US-1.2": "Profile edits"}
    assert (record["run"], record["snapshot_sha256"]) == (RUN1, SNAP1)
    assert record["decisive_value"] == 0.91
    assert datetime.fromisoformat(record["decided_at"])


def test_each_apply_is_one_batch_keyed_by_proposal_keys(store: DecisionStore) -> None:
    """§3 History, Keys: rev += 1 per POST; history holds {key, before, after}; no run-local IDs."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "accept")])
    rev = store.apply(
        RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "reject"), change(SPLIT, "accept")]
    )

    doc = store.read()
    assert rev == doc["rev"] == 2
    assert [batch["rev"] for batch in doc["history"]] == [1, 2]
    assert all(batch["at"] for batch in doc["history"])
    second = {c["key"]: c for c in doc["history"][1]["changes"]}
    assert set(second) == {MERGE_A, SPLIT}
    before, after = second[MERGE_A]["before"], second[MERGE_A]["after"]
    assert before is not None and after is not None
    assert (before["action"], after["action"]) == ("accept", "reject")
    assert second[SPLIT]["before"] is None
    assert set(doc["decisions"]) == {MERGE_A, SPLIT}
    history_keys = {c["key"] for batch in doc["history"] for c in batch["changes"]}
    assert history_keys.isdisjoint(RUN_LOCAL_IDS)


def test_note_alone_keeps_action_null_and_clear_resets_it(store: DecisionStore) -> None:
    """§2 Review keys: a note alone keeps `action: null`; `clear` removes the action."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(SPLIT, note="ask the PO")])
    noted = store.read()["decisions"][SPLIT]
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(SPLIT, "accept")])
    accepted = store.read()["decisions"][SPLIT]
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(SPLIT, "clear")])
    cleared = store.read()["decisions"][SPLIT]

    assert (noted["action"], noted["note"]) == (None, "ask the PO")
    assert (accepted["action"], accepted["note"]) == ("accept", "ask the PO")
    assert cleared["action"] is None


def test_undo_appends_inverse_batches_until_nothing_is_left(
    store: DecisionStore, path: Path
) -> None:
    """§3 History: undo appends the inverse of the last batch with `undo_of`; then 409."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "accept")])
    after_first = store.read()["decisions"]
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "reject"), change(SPLIT, "defer")])
    second_batch = store.read()["history"][1]

    rev = store.undo()

    doc = store.read()
    assert rev == doc["rev"] == 3
    inverse = doc["history"][-1]
    assert inverse["undo_of"] == 2
    expected = {c["key"]: (c["after"], c["before"]) for c in second_batch["changes"]}
    assert {c["key"]: (c["before"], c["after"]) for c in inverse["changes"]} == expected
    assert doc["decisions"] == after_first

    assert store.undo() == 4
    assert store.read()["history"][-1]["undo_of"] == 1
    assert store.read()["decisions"] == {}
    with pytest.raises(ApiError) as excinfo:
        store.undo()
    assert excinfo.value.code is ErrorCode.NOTHING_TO_UNDO
    with pytest.raises(ApiError) as empty:
        DecisionStore(path.with_name("empty.json")).undo()
    assert empty.value.code is ErrorCode.NOTHING_TO_UNDO
    assert not path.with_name("empty.json").exists()


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param([change("M1", "accept")], id="run-local-merge-id"),
        pytest.param([change("S1", "accept")], id="run-local-split-id"),
        pytest.param([change("O1", "reject")], id="run-local-overlap-id"),
        pytest.param([change("merge:US-1.2>US-1.1", "accept")], id="reversed-merge"),
        pytest.param([change("overlap:US-2.2+US-1.3", "accept")], id="unsorted-overlap"),
        pytest.param([change("split:US-9.9", "accept")], id="unknown-story"),
        pytest.param([change(MERGE_A, "accept"), change("M2", "accept")], id="mixed-batch"),
    ],
)
def test_unknown_keys_reject_the_whole_batch(
    store: DecisionStore, path: Path, changes: list[DecisionChange]
) -> None:
    """§3 Keys, §7.2: only the run's proposal keys; 422 `unknown_key`; nothing is written."""
    with pytest.raises(ApiError) as excinfo:
        store.apply(RUN1, RUN1_DERIVED, SNAP1, changes)

    assert excinfo.value.code is ErrorCode.UNKNOWN_KEY
    assert not path.exists() or store.read()["rev"] == 0


# --- writes ---------------------------------------------------------------------------------------


def test_failed_replace_keeps_the_old_file(
    store: DecisionStore, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3 Writes: `.tmp` then `os.replace`; if the replace fails the old file is intact."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "accept")])
    old = path.read_bytes()

    def failing_replace(src: object, dst: object) -> None:
        raise OSError("simulated failure of os.replace")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises((OSError, ApiError)):
        store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(SPLIT, "accept")])
    monkeypatch.undo()

    assert path.read_bytes() == old
    assert DecisionStore(path).read()["rev"] == 1
    assert store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(SPLIT, "accept")]) == 2


def test_write_keeps_a_backup_of_the_previous_file(store: DecisionStore, path: Path) -> None:
    """§3 Writes: the previous version is copied to `.bak` before each write."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "accept")])
    first = path.read_bytes()

    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(SPLIT, "accept")])

    backups = [p for p in path.parent.iterdir() if p.name.endswith(".bak")]
    assert [p.read_bytes() for p in backups] == [first]


def test_hand_edits_are_reloaded_before_the_next_write(store: DecisionStore, path: Path) -> None:
    """§3 Writes: a changed mtime or size means re-read first; hand edits win."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "accept")])
    doc = json.loads(path.read_text("utf-8"))
    doc["decisions"][MERGE_A]["note"] = "edited by hand, keep me"
    path.write_text(json.dumps(doc, indent=4), "utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    seen = store.read()["decisions"][MERGE_A]["note"]
    store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(SPLIT, "accept")])

    after = json.loads(path.read_text("utf-8"))
    assert seen == "edited by hand, keep me"
    assert after["decisions"][MERGE_A]["note"] == "edited by hand, keep me"
    assert set(after["decisions"]) == {MERGE_A, SPLIT}
    assert after["rev"] == 2


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b'{"format": "tesselint/decisions", "rev": ', id="truncated"),
        pytest.param(b"\xff\xfe\x00 not json", id="binary"),
        pytest.param(
            b'{"format": "other", "version": 1, "rev": 3, "decisions": {}, "history": []}',
            id="wrong-format",
        ),
        pytest.param(
            b'{"format": "tesselint/decisions", "version": 9, "rev": 3, "decisions": {},'
            b' "history": []}',
            id="future-version",
        ),
        pytest.param(b"[]", id="not-an-object"),
    ],
)
def test_unreadable_file_blocks_writes_and_is_never_overwritten(path: Path, content: bytes) -> None:
    """§3 Writes: 500 `decisions_unreadable` on read, apply and undo; the bytes stay as is."""
    path.write_bytes(content)
    store = DecisionStore(path)

    attempts = (
        store.read,
        lambda: store.apply(RUN1, RUN1_DERIVED, SNAP1, [change(MERGE_A, "accept")]),
        store.undo,
    )
    for attempt in attempts:
        with pytest.raises(ApiError) as excinfo:
            attempt()
        assert excinfo.value.code is ErrorCode.DECISIONS_UNREADABLE

    assert path.read_bytes() == content


# --- computed views -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "status", "changed", "proposed"),
    [
        (MERGE_A, "stale", ["US-1.1"], True),
        (MERGE_B, "current", [], True),
        (SPLIT, "missing", None, False),
        (OVERLAP, "current", [], False),
    ],
)
def test_views_compute_status_against_another_run(
    store: DecisionStore, key: str, status: str, changed: list[str] | None, proposed: bool
) -> None:
    """§3 Computed: current, stale (`changed` names the story), missing, orphaned (not proposed)."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, ALL_FOUR)

    views = views_by_key(store.views(RUN2_DERIVED, None))
    view = views[key]

    assert view["status"] == status
    assert view["proposed"] is proposed
    if changed is not None:
        assert view["changed"] == changed
    if status == "stale":
        assert view.get("old_texts", {}).get("US-1.1") == render(S11, "full")
    assert view["run"] == RUN1
    assert set(views) == {MERGE_A, MERGE_B, SPLIT, OVERLAP}  # none is dropped (§3)


def test_accepting_one_option_supersedes_the_other(store: DecisionStore) -> None:
    """§2 Review: accepting a merge of US-1.2 derives `superseded_by` on the other option."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, ALL_FOUR)

    views = views_by_key(store.views(RUN1_DERIVED, None))

    assert views[MERGE_B]["superseded_by"] == MERGE_A
    assert views[MERGE_A]["superseded_by"] is None
    assert views[SPLIT]["superseded_by"] is None
    assert all(v["status"] == "current" and v["proposed"] for v in views.values())


@pytest.mark.parametrize(
    ("tombstone_target", "applied_a", "applied_b"),
    [("US-1.1", True, False), ("US-1.3", False, True), (None, False, False)],
)
def test_applied_follows_the_source_tombstone(
    store: DecisionStore,
    tombstone_target: str | None,
    applied_a: bool,
    applied_b: bool,
) -> None:
    """§3 Computed: `applied` (merges only) when the source holds `drop — merged into keep`."""
    store.apply(RUN1, RUN1_DERIVED, SNAP1, ALL_FOUR)
    tombstones = (
        (Tombstone(id="US-1.2", epic="E1", title="Profile edits", target=tombstone_target, line=9),)
        if tombstone_target
        else ()
    )
    stories = (S11, S13, S21, S22) if tombstone_target else (S11, S12, S13, S21, S22)
    source = ParseResult(stories=stories, tombstones=tombstones, unparsed=())

    views = views_by_key(store.views(RUN1_DERIVED, source))

    assert views[MERGE_A]["applied"] is applied_a
    assert views[MERGE_B]["applied"] is applied_b
    assert views[SPLIT]["applied"] is None
    assert views[OVERLAP]["applied"] is None


# --- the lock -------------------------------------------------------------------------------------


def dead_pid() -> int:
    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(proc.stdout)


def test_second_serve_exits_2_while_the_lock_pid_lives(tmp_path: Path) -> None:
    """§3 Locking: `.lock` holds a live pid, so a second `serve` exits 2 and leaves it alone."""
    (tmp_path / "runs").mkdir()
    lock = tmp_path / "state" / LOCK_FILE
    lock.parent.mkdir()
    lock.write_text(str(os.getpid()), "utf-8")

    proc = subprocess.run(
        serve_argv(tmp_path),
        cwd=tmp_path,
        env=serve_env({}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 2, proc.stderr
    assert lock.read_text("utf-8").strip() == str(os.getpid())


def test_serve_takes_over_a_lock_whose_pid_is_dead(tmp_path: Path) -> None:
    """§3 Locking: a stale `.lock` (dead pid) does not block `serve`, which writes its own pid."""
    lock = tmp_path / "state" / LOCK_FILE
    lock.parent.mkdir()
    lock.write_text(str(dead_pid()), "utf-8")

    with serve(tmp_path) as served:
        assert lock.read_text("utf-8").strip() == str(served.proc.pid)
