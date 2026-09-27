"""Review cases: one merge group per dropped story, one case per split or overlap (§4.3).

Spec: docs/ui/DESIGN.md §2 Review, §4.3, §7.3 `Case`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from tesselint.models import ProposalKind
from tesselint.server.payloads import Case, Kind

if TYPE_CHECKING:
    from tesselint.proposals import Proposal

MERGE_GROUP_PREFIX = "mg:"  # a merge group's key is `mg:<drop>`
CONFLICT_PREFIX = "⚠ conflicts with "  # the evidence line propose() adds (proposals.py)
REVIEWED = (ProposalKind.MERGE, ProposalKind.SPLIT, ProposalKind.OVERLAP)


def build_cases(
    proposals: Sequence[Proposal],
    order: Mapping[str, int],
    *,
    epics: Mapping[str, str] | None = None,
) -> list[Case]:
    """Cases with strength, conflicts and document `order` (story ID → index) (§4.3).

    A merge group (`mg:<drop>`) lists its merges by m, strongest first, and takes the strongest
    one's m and borderline flag; a split or overlap case is its proposal. `anchor` is the dropped
    story, the split story or the overlap's first member in document order, and `order` is the
    anchor's index. `conflicts` are the keys of other cases one of its proposals conflicts with.
    `epics` maps story IDs to epics (additive; default: `E<n>` from `US-<n>.<m>`). Cases come in
    the default Review order: strength desc, then document order, then key.
    """
    reviewed = [p for p in proposals if p.kind in REVIEWED]
    far = len(order)
    groups: dict[str, list[Proposal]] = {}
    for p in reviewed:
        if p.kind is ProposalKind.MERGE:
            groups.setdefault(p.stories[1], []).append(p)
    case_of = {p.key: case_key(p) for p in reviewed}
    conflicts = proposal_conflicts(proposals)

    def make(key: str, kind: Kind, anchor: str, options: Sequence[Proposal]) -> Case:
        stories = sorted({s for p in options for s in p.stories}, key=lambda s: order.get(s, far))
        linked = [case_of[k] for p in options for k in conflicts.get(p.key, ()) if k in case_of]
        return Case(
            key=key,
            kind=kind,
            anchor=anchor,
            proposals=[p.key for p in options],
            strength=options[0].decisive_value,
            borderline=options[0].borderline,
            epics=list(dict.fromkeys(_epic(s, epics) for s in stories)),
            conflicts=[k for k in dict.fromkeys(linked) if k != key],
            order=order.get(anchor, far),
        )

    cases = [
        make(MERGE_GROUP_PREFIX + drop, "merge", drop, sorted(merges, key=_strongest))
        for drop, merges in groups.items()
    ]
    for p in reviewed:
        if p.kind is not ProposalKind.MERGE:
            anchor = min(p.stories, key=lambda s: order.get(s, far))
            kind: Kind = "split" if p.kind is ProposalKind.SPLIT else "overlap"
            cases.append(make(p.key, kind, anchor, [p]))
    cases.sort(key=lambda c: (-c["strength"], c["order"], c["key"]))
    return cases


def _strongest(p: Proposal) -> float:
    """Sort key: the highest decisive value first (a stable sort keeps ties in order)."""
    return -p.decisive_value


def case_key(p: Proposal) -> str:
    """The key of the case a proposal belongs to: `mg:<drop>` for a merge, else its own key."""
    return MERGE_GROUP_PREFIX + p.stories[1] if p.kind is ProposalKind.MERGE else p.key


def proposal_conflicts(proposals: Sequence[Proposal]) -> dict[str, list[str]]:
    """Proposal key → keys of the proposals its "⚠ conflicts with" evidence line names.

    propose() names them by run-local ID (M3, S1); keys are stable across runs (§3 Keys).
    """
    key_of = {p.id: p.key for p in proposals}
    found: dict[str, list[str]] = {}
    for p in proposals:
        for line in p.evidence:
            if line.startswith(CONFLICT_PREFIX):
                ids = line.removeprefix(CONFLICT_PREFIX).split(", ")
                found[p.key] = [key_of[i] for i in ids if i in key_of]
    return found


def _epic(story_id: str, epics: Mapping[str, str] | None) -> str:
    if epics is not None and story_id in epics:
        return epics[story_id]
    return "E" + story_id.removeprefix("US-").split(".")[0]
