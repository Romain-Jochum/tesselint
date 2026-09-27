"""Differences between two runs (docs/ui/DESIGN.md §2 Runs, §4.3, §7.3 `RunDiff`)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, overload

from tesselint.models import ProposalKind
from tesselint.proposals import proposal_key
from tesselint.render import render
from tesselint.server.payloads import EditedStory, RunDiff

if TYPE_CHECKING:
    from tesselint.derive import Derived
    from tesselint.proposals import Proposal

CHANGED_MIN_DELTA = 0.05  # a proposal changed if |Δ decisive value| ≥ this or borderline flips
_EPS = 1e-9  # 0.85 - 0.80 is 0.0499…; float noise must not hide a 0.05 change


@overload
def diff_runs(a: Derived, b: Derived, decisions: Mapping[str, Any] | None = None) -> RunDiff: ...


@overload
def diff_runs(*runs: Derived, decisions: Mapping[str, Any] | None = None) -> RunDiff: ...


def diff_runs(*args: Any, **kwargs: Any) -> RunDiff:
    """Stories added, removed, edited, renamed; proposals new, gone, changed (§4.3).

    Stories compare by ID on their full rendered text; a renamed story is a removed story's text
    under a new ID, and is neither added nor removed. Proposals compare by key. With a
    `decisions.json` document (PLAN resolution 7), `decisions_to_review` lists the decisions that
    name an edited, removed or renamed story; without one it is empty. Every list is sorted.
    """
    if len(args) == 2:
        a, b = args[0], args[1]
        decisions = kwargs.get("decisions")
    elif len(args) == 3:
        a, b = args[0], args[1]
        decisions = args[2]
    else:
        raise TypeError(f"diff_runs expects 2 runs, got {len(args)} positional arguments")
    old = {s.id: render(s) for s in a.stories}
    new = {s.id: render(s) for s in b.stories}
    gone_ids = sorted(old.keys() - new.keys())
    came_ids = sorted(new.keys() - old.keys())
    renamed = [(o, n) for o in gone_ids for n in came_ids if old[o] == new[n]]
    renamed_old, renamed_new = {o for o, _ in renamed}, {n for _, n in renamed}
    removed = [sid for sid in gone_ids if sid not in renamed_old]
    edited = [
        EditedStory(id=sid, old_text=old[sid], new_text=new[sid])
        for sid in sorted(old.keys() & new.keys())
        if old[sid] != new[sid]
    ]

    pa, pb = _by_key(a), _by_key(b)
    changed = [
        key
        for key in sorted(pa.keys() & pb.keys())
        if abs(pa[key].decisive_value - pb[key].decisive_value) >= CHANGED_MIN_DELTA - _EPS
        or pa[key].borderline != pb[key].borderline
    ]

    touched = {e["id"] for e in edited} | set(removed) | renamed_old
    return RunDiff(
        added=[sid for sid in came_ids if sid not in renamed_new],
        removed=removed,
        renamed=renamed,
        edited=edited,
        new=sorted(pb.keys() - pa.keys()),
        gone=sorted(pa.keys() - pb.keys()),
        changed=changed,
        decisions_to_review=_to_review(decisions, touched),
    )


def _by_key(run: Derived) -> dict[str, Proposal]:
    """The run's merge, overlap and split proposals by key (leave-alone rows are not proposals)."""
    return {
        p.key or proposal_key(p.kind, p.target, p.stories): p
        for p in run.proposals
        if p.kind is not ProposalKind.LEAVE
    }


def _to_review(decisions: Mapping[str, Any] | None, touched: set[str]) -> list[str]:
    """Keys of the stored decisions that name a touched story, sorted."""
    records = decisions.get("decisions") if decisions is not None else None
    if not isinstance(records, Mapping):
        return []
    return sorted(
        key
        for key, record in records.items()
        if isinstance(record, Mapping) and touched & set(record.get("stories") or ())
    )
