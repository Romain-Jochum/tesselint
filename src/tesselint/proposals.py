"""Merge, overlap, split and leave-alone proposals (DESIGN §2.5, §9 proposals.md)."""

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tesselint.cluster import Cluster
from tesselint.models import ProposalKind, Relation, Story, Thresholds
from tesselint.prefilter import rounded_cos
from tesselint.render import render
from tesselint.verdicts import REL_ORDER, PairVerdict

Verdicts = Mapping[tuple[str, str], PairVerdict]
# Pre-filter result per pair, as write_outputs takes it: (cosine, kept); either key order works.
Prefilter = Mapping[tuple[str, str], tuple[float, bool]]

# A merge's first evidence item names its relation; render_proposals moves it into the heading.
MERGE_RELATIONS = ("duplicate", "containment")
RELATED_CAP = 5  # "also related" and "edges" show this many, then "+N more"
HUB_MIN = 3  # proposals.md lists a story kept by this many merges (primary or folded) as a hub
SUMMARY_TOP = 10  # the proposals.md summary lists this many merges and splits
CALIBRATION_NOT_RUN = "not run (tesselint eval RUN --make-labels)"
_EPS = 1e-9

_SHORT = {
    Relation.DUPLICATE: "dup",
    Relation.A_CONTAINS_B: "A⊃B",
    Relation.B_CONTAINS_A: "B⊃A",
    Relation.PARTIAL: "partial",
    Relation.SAME_AREA: "same-area",
    Relation.UNRELATED: "unrelated",
}


@dataclass(frozen=True, slots=True)
class Proposal:
    """One proposal; `decisive_value` is the number its threshold was tested against."""

    id: str
    kind: ProposalKind
    target: str | None
    stories: tuple[str, ...]
    decisive_value: float
    borderline: bool
    evidence: tuple[str, ...]
    key: str = ""  # proposal_key(); filled by propose() (docs/ui/DESIGN.md §3, §7.1)
    details: Mapping[str, Any] | None = None  # structured evidence (docs/ui/DESIGN.md §7.3)


KEY_RESERVED = (">", "+", ":")  # separators of a proposal key; no story ID may hold one


def proposal_key(kind: ProposalKind, target: str | None, stories: Sequence[str]) -> str:
    """Stable decision key, e.g. `merge:<keep>><drop>` (docs/ui/DESIGN.md §3 Keys).

    `merge:<keep>><drop>` (stories are keep, drop), `split:<id>`, `overlap:` + the member IDs
    sorted and joined by `+`, and `leave:<id>`. Run-local IDs (M1, C3) never appear, so a key
    names the same proposal in every run. `target` is not part of any key.
    """
    bad = [sid for sid in stories if not sid or any(ch in sid for ch in KEY_RESERVED)]
    if bad:
        raise ValueError(f"story ID {bad[0]!r} cannot be part of a proposal key (no > + :)")
    if kind == ProposalKind.OVERLAP and stories:
        return "overlap:" + "+".join(sorted(stories))
    if kind == ProposalKind.MERGE and len(stories) == 2:
        return f"merge:{stories[0]}>{stories[1]}"
    if kind in (ProposalKind.SPLIT, ProposalKind.LEAVE) and len(stories) == 1:
        return f"{kind.value}:{stories[0]}"
    raise ValueError(f"a {kind.value} proposal cannot have stories {list(stories)!r}")


@dataclass(frozen=True, slots=True)
class _Merge:
    keep: str
    drop: str
    verdict: PairVerdict
    relation: str


@dataclass(frozen=True, slots=True)
class _Split:
    story: Story
    score: float
    borderline: bool
    reached: tuple[str, ...]


def propose(
    stories: Sequence[Story],
    verdicts: Verdicts,
    clusters: Sequence[Cluster],
    atomic: Mapping[str, float | None],
    th: Thresholds,
    *,
    prefilter: Prefilter | None = None,
) -> list[Proposal]:
    """Derive proposals from verdicts, clusters and atomic.v2 scores using `th` (DESIGN §2.5).

    Each kind is sorted by its decisive value, highest first, and numbered in that order. Merges
    that drop an already-dropped story, and merges and splits on the same story, are marked
    "⚠ conflicts with …". `prefilter` adds the pair's cosine to merge evidence.
    """
    ctx = _Context(stories, verdicts, clusters, atomic, th, prefilter or {})
    merges = ctx.merges()
    splits = ctx.splits()
    merge_ids = [f"M{i}" for i in range(1, len(merges) + 1)]
    split_ids = {s.story.id: f"S{i}" for i, s in enumerate(splits, start=1)}
    conflicts = _conflicts(merges, merge_ids, split_ids)

    proposals = [
        _keyed(
            pid=mid,
            kind=ProposalKind.MERGE,
            target=mg.keep,
            stories=(mg.keep, mg.drop),
            decisive_value=mg.verdict.merge,
            borderline=_borderline(mg.verdict.merge, th.merge, th),
            evidence=(*ctx.merge_evidence(mg), *_conflict_line(conflicts.get(mid))),
            details=ctx.merge_details(mg, merges),
        )
        for mid, mg in zip(merge_ids, merges, strict=True)
    ]
    merged_sets = [{mg.keep, mg.drop} for mg in merges]
    open_clusters = [c for c in clusters if not any(set(c.members) <= ms for ms in merged_sets)]
    open_clusters.sort(key=lambda c: -c.mean_overlap)
    proposals += [
        _keyed(
            pid=f"O{i}",
            kind=ProposalKind.OVERLAP,
            target=c.id,
            stories=tuple(c.members),
            decisive_value=c.mean_overlap,
            borderline=_borderline(c.mean_overlap, th.cluster, th),
            evidence=ctx.overlap_evidence(c),
            details=ctx.overlap_details(c),
        )
        for i, c in enumerate(open_clusters, start=1)
    ]
    proposals += [
        _keyed(
            pid=split_ids[sp.story.id],
            kind=ProposalKind.SPLIT,
            target=None,
            stories=(sp.story.id,),
            decisive_value=sp.score,
            borderline=sp.borderline,
            evidence=(
                *ctx.split_evidence(sp),
                *_conflict_line(conflicts.get(split_ids[sp.story.id])),
            ),
            details=ctx.split_details(sp, merges),
        )
        for sp in splits
    ]
    acted = {sid for p in proposals for sid in p.stories}
    leave = [s.id for s in stories if s.id not in acted]
    proposals += [
        _keyed(f"L{i}", ProposalKind.LEAVE, None, (sid,), 0.0, False, ())
        for i, sid in enumerate(leave, start=1)
    ]
    return proposals


def _keyed(
    pid: str,
    kind: ProposalKind,
    target: str | None,
    stories: tuple[str, ...],
    decisive_value: float,
    borderline: bool,
    evidence: tuple[str, ...],
    details: dict[str, Any] | None = None,
) -> Proposal:
    """A Proposal with its `proposal_key` filled in."""
    key = proposal_key(kind, target, stories)
    return Proposal(pid, kind, target, stories, decisive_value, borderline, evidence, key, details)


class _Context:
    """The inputs of one propose() call and the evidence builders that read them."""

    def __init__(
        self,
        stories: Sequence[Story],
        verdicts: Verdicts,
        clusters: Sequence[Cluster],
        atomic: Mapping[str, float | None],
        th: Thresholds,
        prefilter: Prefilter,
    ) -> None:
        self.stories = stories
        self.by_id = {s.id: s for s in stories}
        self.doc = {s.id: i for i, s in enumerate(stories)}
        self.verdicts = verdicts
        self.clusters = clusters
        self.cluster_of = {m: c.id for c in clusters for m in c.members}
        self.atomic = atomic
        self.th = th
        self.prefilter = prefilter

    def verdict(self, x: str, y: str) -> PairVerdict | None:
        """The verdict of a pair whatever order it is keyed in."""
        return self.verdicts.get((x, y)) or self.verdicts.get((y, x))

    def merges(self) -> list[_Merge]:
        """Pairs with m >= threshold, strongest first; ties keep document order (§2.5)."""
        found: list[_Merge] = []
        far = len(self.doc)
        pairs = sorted(
            self.verdicts, key=lambda p: (self.doc.get(p[0], far), self.doc.get(p[1], far))
        )
        for pair in pairs:
            v = self.verdicts[pair]
            if v.merge < self.th.merge:
                continue
            dup = v.probs.get(Relation.DUPLICATE, 0.0)
            a_in_b = v.probs.get(Relation.A_CONTAINS_B, 0.0)
            b_in_a = v.probs.get(Relation.B_CONTAINS_A, 0.0)
            if a_in_b > b_in_a and a_in_b >= dup:
                found.append(_Merge(v.a, v.b, v, "containment"))
            elif b_in_a > a_in_b and b_in_a >= dup:
                found.append(_Merge(v.b, v.a, v, "containment"))
            else:  # a duplicate: the later story merges into the earlier one
                found.append(_Merge(v.a, v.b, v, "duplicate"))
        found.sort(key=lambda mg: -mg.verdict.merge)
        return found

    def splits(self) -> list[_Split]:
        """atomic >= split, or >= split_corroborated with o edges into 2+ other clusters (§2.5).

        The word gate counts the full text, as the atomic.v2 call does (§2.4).
        """
        found: list[_Split] = []
        for s in self.stories:
            score = self.atomic.get(s.id)
            if score is None or len(render(s).split()) < self.th.min_words_atomic:
                continue
            reached = self.reached_clusters(s.id)
            if score >= self.th.split:
                found.append(_Split(s, score, _borderline(score, self.th.split, self.th), reached))
            elif score >= self.th.split_corroborated and len(reached) >= 2:
                borderline = _borderline(score, self.th.split_corroborated, self.th)
                found.append(_Split(s, score, borderline, reached))
        found.sort(key=lambda sp: -sp.score)
        return found

    def reached_clusters(self, sid: str) -> tuple[str, ...]:
        """Clusters other than the story's own that one of its o >= cluster edges reaches."""
        reached = []
        for c in self.clusters:
            if sid in c.members:
                continue
            edges = (self.verdict(sid, m) for m in c.members)
            if any(v is not None and v.overlap >= self.th.cluster for v in edges):
                reached.append(c.id)
        return tuple(reached)

    def merge_evidence(self, mg: _Merge) -> tuple[str, ...]:
        """Relation, kept/dropped titles, top-2 per order, o, cluster, cosine, also related."""
        v = mg.verdict
        numbers = [f"fwd {_top2(v.fwd)}", f"swap {_top2(v.swap)}" if v.swap else "no swap"]
        numbers += [f"o {v.overlap:.2f}", self.cluster_note(mg.keep, mg.drop)]
        cos = self.prefilter.get((v.a, v.b)) or self.prefilter.get((v.b, v.a))
        if cos is not None:
            numbers.append(f"cos {rounded_cos(cos[0]):.2f}")
        keep_drop = f"keep {self.named(mg.keep, True)} · drop {self.named(mg.drop, True)}"
        related = self.related({mg.keep, mg.drop})
        return (mg.relation, keep_drop, " · ".join(numbers), *related)

    def overlap_evidence(self, c: Cluster) -> tuple[str, ...]:
        """Member titles, the cluster's edges by o (highest first) and outside related stories."""
        members = c.members
        edges = self.cluster_edges(c)
        lines = [" · ".join(self.named(m) for m in members)]
        if edges:
            lines.append("edges: " + _capped([_edge(v) for v in edges]))
        return (*lines, *self.related(set(members)))

    def split_evidence(self, sp: _Split) -> tuple[str, ...]:
        """Title, words, sub-bullets, own cluster, clusters its o edges reach, tag."""
        s = sp.story
        n_subs = len(s.subs)
        facts = [  # the bundled score is the decisive value, shown in the heading
            f"{len(render(s).split())} words",
            f"{n_subs} sub-bullet{'' if n_subs == 1 else 's'}",
        ]
        if s.id in self.cluster_of:
            facts.append(f"in {self.cluster_of[s.id]}")
        if sp.reached:
            facts.append(f"overlaps {', '.join(sp.reached)}")
        if s.tag:
            facts.append(f"tag [{s.tag}]")
        return (self.named(s.id), " · ".join(facts))

    def related(self, members: Collection[str]) -> tuple[str, ...]:
        """ "also related": outside stories with o >= related to a member, strongest edge each."""
        found = self.related_verdicts(members)
        if not found:
            return ()
        return ("also related: " + _capped([_edge(v) for v in found]),)

    def related_verdicts(self, members: Collection[str]) -> list[PairVerdict]:
        """The strongest edge of each outside story with o >= related to a member, by o."""
        found: list[PairVerdict] = []
        for s in self.stories:
            if s.id in members:
                continue
            edges = [v for m in members if (v := self.verdict(s.id, m)) is not None]
            best = max(edges, key=lambda v: v.overlap, default=None)
            if best is not None and best.overlap >= self.th.related:
                found.append(best)
        found.sort(key=lambda v: -v.overlap)
        return found

    def cluster_edges(self, c: Cluster) -> list[PairVerdict]:
        """The verdicts between a cluster's members, highest o first."""
        members = c.members
        edges = [
            v
            for i, a in enumerate(members)
            for b in members[i + 1 :]
            if (v := self.verdict(a, b)) is not None
        ]
        edges.sort(key=lambda v: -v.overlap)
        return edges

    # Structured twins of the evidence lines (docs/ui/DESIGN.md §7.3), uncapped.

    def merge_details(self, mg: _Merge, merges: Sequence[_Merge]) -> dict[str, Any]:
        """MergeDetails; fwd and swap are in document order (A is the earlier story)."""
        v = mg.verdict
        cos = self.prefilter.get((v.a, v.b)) or self.prefilter.get((v.b, v.a))
        return {
            "relation": mg.relation,
            "keep": mg.keep,
            "drop": mg.drop,
            "m": v.merge,
            "o": v.overlap,
            "fwd": _probs(v.fwd),
            "swap": None if v.swap is None else _probs(v.swap),
            "cluster_keep": self.cluster_of.get(mg.keep),
            "cluster_drop": self.cluster_of.get(mg.drop),
            "cos": None if cos is None else rounded_cos(cos[0]),
            "atomic_keep": self.atomic.get(mg.keep),
            "atomic_drop": self.atomic.get(mg.drop),
            "related": [_edge_row(e) for e in self.related_verdicts({mg.keep, mg.drop})],
            "alternatives": [o.keep for o in merges if o.drop == mg.drop and o is not mg],
        }

    def overlap_details(self, c: Cluster) -> dict[str, Any]:
        """OverlapDetails."""
        return {
            "cluster": c.id,
            "members": list(c.members),
            "mean_o": c.mean_overlap,
            "edges": [_edge_row(e) for e in self.cluster_edges(c)],
            "related": [_edge_row(e) for e in self.related_verdicts(set(c.members))],
        }

    def split_details(self, sp: _Split, merges: Sequence[_Merge]) -> dict[str, Any]:
        """SplitDetails; `keeps` are the stories the split story would absorb by a merge."""
        s = sp.story
        return {
            "story": s.id,
            "bundled": sp.score,
            "words": len(render(s).split()),
            "n_subs": len(s.subs),
            "cluster": self.cluster_of.get(s.id),
            "reaches": list(sp.reached),
            "keeps": [mg.drop for mg in merges if mg.keep == s.id],
        }

    def cluster_note(self, keep: str, drop: str) -> str:
        kept_in, dropped_in = self.cluster_of.get(keep), self.cluster_of.get(drop)
        if kept_in is not None and kept_in == dropped_in:
            return kept_in
        notes = [
            f"{role} in {cid}" for role, cid in (("keep", kept_in), ("drop", dropped_in)) if cid
        ]
        return " · ".join(notes) or "no cluster"

    def named(self, sid: str, with_score: bool = False) -> str:
        story = self.by_id.get(sid)
        text = f'{sid} "{story.title}"' if story else sid
        score = self.atomic.get(sid)
        if with_score and score is not None:
            text += f" (bundled {score:.2f})"
        return text


def _borderline(value: float, threshold: float, th: Thresholds) -> bool:
    """The deciding value is within `th.borderline` of its threshold (§2.5)."""
    return abs(value - threshold) <= th.borderline + _EPS


def _top2(probs: Mapping[Relation, float]) -> str:
    """The two likeliest relations, ties in option order, e.g. `partial 0.53 / A⊃B 0.37`."""
    top = sorted(REL_ORDER, key=lambda r: -probs.get(r, 0.0))[:2]
    return " / ".join(f"{_SHORT[r]} {probs.get(r, 0.0):.2f}" for r in top)


def _edge(v: PairVerdict) -> str:
    """A pair as `A ⊃ B`, `A = B` (duplicate) or `A ~ B` (anything else), with its o."""
    if v.relation == Relation.A_CONTAINS_B:
        pair = f"{v.a} ⊃ {v.b}"
    elif v.relation == Relation.B_CONTAINS_A:
        pair = f"{v.b} ⊃ {v.a}"
    elif v.relation == Relation.DUPLICATE:
        pair = f"{v.a} = {v.b}"
    else:
        pair = f"{v.a} ~ {v.b}"
    return f"{pair} (o {v.overlap:.2f})"


def _edge_row(v: PairVerdict) -> dict[str, Any]:
    """An Edge (docs/ui/DESIGN.md §7.3), as clusters.json writes one."""
    relation = v.relation.value
    return {"a": v.a, "b": v.b, "overlap": v.overlap, "merge": v.merge, "relation": relation}


def _probs(probs: Mapping[Relation, float]) -> dict[str, float]:
    """Probs keyed by the rel.v1 option names, in option order."""
    return {r.value: probs.get(r, 0.0) for r in REL_ORDER}


def _capped(items: Sequence[str]) -> str:
    shown = list(items[:RELATED_CAP])
    if len(items) > RELATED_CAP:
        shown.append(f"+{len(items) - RELATED_CAP} more")
    return " · ".join(shown)


def _conflicts(
    merges: Sequence[_Merge], merge_ids: Sequence[str], split_ids: Mapping[str, str]
) -> dict[str, list[str]]:
    """Proposal id -> ids it contradicts (H4).

    The strongest merge of a dropped story stands; later merges of that story conflict with it.
    A merge and a split that name the same story conflict with each other.
    """
    first_drop: dict[str, str] = {}
    conflicts: dict[str, list[str]] = {}
    for mid, mg in zip(merge_ids, merges, strict=True):
        found = conflicts.setdefault(mid, [])
        if mg.drop in first_drop:
            found.append(first_drop[mg.drop])
        else:
            first_drop[mg.drop] = mid
        for sid in (mg.keep, mg.drop):
            if sid in split_ids and split_ids[sid] not in found:
                found.append(split_ids[sid])
                conflicts.setdefault(split_ids[sid], []).append(mid)
    return conflicts


def _conflict_line(ids: Sequence[str] | None) -> tuple[str, ...]:
    return (f"⚠ conflicts with {', '.join(ids)}",) if ids else ()


def render_proposals(
    proposals: Sequence[Proposal],
    stories: Sequence[Story],
    header: Mapping[str, str],
    *,
    th: Thresholds | None = None,
) -> str:
    """Render proposals.md (DESIGN §9) for the reviewer.

    `header` keys: run_id, snapshot_sha256, model (resolved model or models) and calibration (a
    `calibration_line`). `th` fills the thresholds in the "How to read this" note. A summary
    follows the header, then Hubs (stories kept by HUB_MIN+ merges). Merge shows one entry per
    merged-away story, its strongest merge, and folds the weaker ones into "also contained by".
    """
    th = th or Thresholds()
    title = ["# tesselint proposals"]
    if run_id := header.get("run_id", ""):
        title.append(f"run {run_id}")
    if sha := header.get("snapshot_sha256", ""):
        title.append(f"snapshot {sha[:8]}…" if len(sha) > 8 else f"snapshot {sha}")
    if model := header.get("model", ""):
        title.append(model)
    calibration = header.get("calibration") or CALIBRATION_NOT_RUN

    def of(kind: ProposalKind) -> list[Proposal]:
        return sorted((p for p in proposals if p.kind == kind), key=lambda p: -p.decisive_value)

    titles = {s.id: s.title for s in stories}
    merges, splits = of(ProposalKind.MERGE), of(ProposalKind.SPLIT)
    overlaps = of(ProposalKind.OVERLAP)
    folded = _fold(merges)
    leave = [sid for p in proposals if p.kind == ProposalKind.LEAVE for sid in p.stories]

    lines = [" · ".join(title), f"Calibration: {calibration}", ""]
    lines += _summary(len(stories), folded, overlaps, splits, len(leave), titles)
    lines += ["", _how_to_read(th)]
    rel_banner = _banner("rel.v1", calibration)
    lines += _hub_section(_hubs(merges), splits, titles, rel_banner)
    alternatives = {f.primary.id: f.alternatives for f in folded}
    lines += _section(
        "## Merge",
        [f.primary for f in folded],
        rel_banner,
        lambda p: _merge_block(p, alternatives[p.id]),
    )
    lines += _section("## Overlap", overlaps, rel_banner, _overlap_block)
    split_banner = _banner("atomic.v2", calibration)
    lines += _section("## Split", splits, split_banner, _split_block)

    epic_of = {s.id: s.epic for s in stories}
    by_epic: dict[str, list[str]] = {}
    for sid in leave:
        by_epic.setdefault(epic_of.get(sid, "?"), []).append(sid)
    lines += ["", f"## Leave alone ({len(leave)})"]
    lines += [f"- {epic}: {', '.join(sids)}" for epic, sids in by_epic.items()]
    return "\n".join(lines) + "\n"


def _how_to_read(th: Thresholds) -> str:
    return (
        "How to read this: o is Jev's probability that two stories overlap at all (duplicate, "
        "containment or partial overlap), and m that one can absorb the other (o minus partial "
        f"overlap). A merge needs m ≥ {th.merge:.2f}; an overlap is a cluster with mean "
        f"o ≥ {th.cluster:.2f} that no single merge covers (merge the stories or move the shared "
        f"part); a split needs bundled ≥ {th.split:.2f}, or ≥ {th.split_corroborated:.2f} when "
        f"the story's o ≥ {th.cluster:.2f} edges reach two other clusters, where bundled is "
        "atomic.v2's probability that the story asks for two or more separate capabilities. "
        f"Borderline means the deciding value is within {th.borderline:.2f} of its threshold, "
        "inside Jev's run-to-run drift: read the stories before acting. In the fwd/swap line, A "
        "is the story that comes first in the document, fwd and swap are the two orders Jev was "
        "asked in (top two answers each) and cos is the local embedding similarity. ⊃ marks a "
        "containment, = a duplicate and ~ any other overlap; also related lists other stories at "
        f"o ≥ {th.related:.2f}. Merge shows each story to merge away once, under its strongest "
        "container, and names its weaker containers, with their ids, in also contained by; Hubs "
        f"lists the stories that contain {HUB_MIN} or more others. Sections run strongest first; "
        "⚠ marks proposals that contradict each other."
    )


@dataclass(frozen=True, slots=True)
class _Folded:
    """A merged-away story's strongest merge and its weaker merges into other containers."""

    primary: Proposal
    alternatives: tuple[Proposal, ...]


def _fold(merges: Iterable[Proposal]) -> list[_Folded]:
    """One entry per merged-away story, strongest first; ties keep input (id) order.

    The first merge of a story is the one propose() lets stand; the later ones are the
    alternatives it marks "⚠ conflicts with" that merge.
    """
    groups: dict[str, list[Proposal]] = {}
    for p in sorted(merges, key=lambda p: -p.decisive_value):
        groups.setdefault(_dropped(p), []).append(p)
    return [_Folded(g[0], tuple(g[1:])) for g in groups.values()]


def _hubs(merges: Iterable[Proposal]) -> list[tuple[str, list[Proposal]]]:
    """Stories kept by HUB_MIN+ merges, primary or folded, most merges first, each strongest first.

    Ties keep the order of each hub's strongest merge.
    """
    kept: dict[str, list[Proposal]] = {}
    for p in sorted(merges, key=lambda p: -p.decisive_value):
        kept.setdefault(_kept(p), []).append(p)
    found = [(sid, ps) for sid, ps in kept.items() if len(ps) >= HUB_MIN]
    return sorted(found, key=lambda hub: -len(hub[1]))


def hub_rows(proposals: Sequence[Proposal]) -> list[dict[str, Any]]:
    """proposals.json `hubs`: the stories proposals.md lists under Hubs (docs/ui/DESIGN.md §4.3).

    `keeps` are the stories a hub would absorb, strongest merge first, primary or folded;
    `split` is the key of the hub's own split proposal, else None.
    """
    merges = [p for p in proposals if p.kind == ProposalKind.MERGE]
    splits = {p.stories[0]: p.key for p in proposals if p.kind == ProposalKind.SPLIT and p.stories}
    return [
        {"story": sid, "keeps": [_dropped(p) for p in kept], "split": splits.get(sid)}
        for sid, kept in _hubs(merges)
    ]


def _kept(p: Proposal) -> str:
    return p.target or (p.stories[0] if p.stories else p.id)


def _dropped(p: Proposal) -> str:
    return p.stories[1] if len(p.stories) >= 2 else p.id


def _relation(p: Proposal) -> str | None:
    return next((e for e in p.evidence if e in MERGE_RELATIONS), None)


def _named(sid: str, titles: Mapping[str, str]) -> str:
    return f'{sid} "{titles[sid]}"' if sid in titles else sid


def _link(pid: str) -> str:
    return f"[{pid}](#{pid})"


def _anchor(pid: str) -> str:
    """The target of the summary's and the hubs' links to a proposal."""
    return f'<a id="{pid}"></a>'


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _with_notes(text: str, *notes: str) -> str:
    kept = [n for n in notes if n]
    return f"{text} ({', '.join(kept)})" if kept else text


def _n_borderline(items: Sequence[Proposal]) -> str:
    n = sum(p.borderline for p in items)
    return f"{n} borderline" if n else ""


def _summary(
    n_stories: int,
    folded: Sequence[_Folded],
    overlaps: Sequence[Proposal],
    splits: Sequence[Proposal],
    n_leave: int,
    titles: Mapping[str, str],
) -> list[str]:
    """Counts by kind, then the SUMMARY_TOP strongest merges and splits, linked to their entries."""
    primaries = [f.primary for f in folded]
    n_alt = sum(len(f.alternatives) for f in folded)
    counts = [
        _count(n_stories, "story", "stories"),
        _with_notes(
            _count(len(folded), "story", "stories") + " to merge away",
            _count(n_alt, "alternative container", "alternative containers") + " folded",
            _n_borderline(primaries),
        ),
        _with_notes(_count(len(overlaps), "overlap", "overlaps"), _n_borderline(overlaps)),
        _with_notes(_count(len(splits), "split", "splits"), _n_borderline(splits)),
        f"{n_leave} leave alone",
    ]
    lines = ["Summary: " + " · ".join(counts)]
    lines += _top_list(
        "merges",
        primaries,
        lambda p: (
            f"{_named(_dropped(p), titles)} → {_named(_kept(p), titles)} · m {p.decisive_value:.2f}"
        ),
    )
    lines += _top_list(
        "splits",
        splits,
        lambda p: f"{_named(p.stories[0], titles)} · bundled {p.decisive_value:.2f}",
    )
    return lines


def _top_list(kind: str, items: Sequence[Proposal], what: Callable[[Proposal], str]) -> list[str]:
    """`Top <kind> (k of n):` then the SUMMARY_TOP strongest `items`, each linked to its entry."""
    if not items:
        return []
    top = items[:SUMMARY_TOP]
    bullets = [f"- {_link(p.id)} {what(p)}{_suffix(p)}" for p in top]
    return ["", f"Top {kind} ({len(top)} of {len(items)}):", *bullets]


def _hub_section(
    hubs: Sequence[tuple[str, Sequence[Proposal]]],
    splits: Sequence[Proposal],
    titles: Mapping[str, str],
    banner: str | None,
) -> list[str]:
    """`## Hubs`, left out when no story is kept by HUB_MIN+ merges."""
    if not hubs:
        return []
    split_of = {p.stories[0]: p for p in splits if p.stories}
    lines = ["", "## Hubs"]
    if banner:
        lines.append(banner)
    lines += [
        "",
        "A hub usually means the hub itself bundles capabilities (see its split proposal, if "
        "any) or the stories it contains restate parts of it.",
        "",
    ]
    for sid, merges in hubs:
        contained = ", ".join(f"{_dropped(p)} (m {p.decisive_value:.2f})" for p in merges)
        line = f"- {_named(sid, titles)} contains {len(merges)}: {contained}"
        if (split := split_of.get(sid)) is not None:
            line += f" · split {_link(split.id)} (bundled {split.decisive_value:.2f})"
        lines.append(line)
    return lines


def _banner(question: str, calibration: str) -> str | None:
    if f"{question} FAIL" in calibration:
        return f"⚠ Not trusted: {question} failed calibration."
    return None


def _section(
    title: str,
    items: Sequence[Proposal],
    banner: str | None,
    block: Callable[[Proposal], list[str]],
) -> list[str]:
    lines = ["", title]
    if banner:
        lines.append(banner)
    if not items:
        lines.append("None.")
    for p in items:
        lines += ["", *block(p)]
    return lines


def _suffix(p: Proposal) -> str:
    return " · borderline" if p.borderline else ""


def _bullets(evidence: Sequence[str]) -> list[str]:
    return [f"- {e}" for e in evidence]


def _merge_block(p: Proposal, alternatives: Sequence[Proposal] = ()) -> list[str]:
    """A merge entry; `alternatives` (weaker merges of the same story) fold into one bullet."""
    relation = _relation(p)
    label = f" ({relation})" if relation else ""
    if len(p.stories) >= 2:
        what = f"merge {p.stories[1]} → {p.stories[0]}{label}"
    else:
        what = f"merge {p.target}{label}"
    heading = f"### {p.id} · {what} · m {p.decisive_value:.2f}{_suffix(p)}"
    evidence = [e for e in p.evidence if e not in MERGE_RELATIONS]
    if alternatives:
        tail = ("also related", "⚠")
        at = next((i for i, e in enumerate(evidence) if e.startswith(tail)), len(evidence))
        evidence.insert(at, _folded_line(alternatives))
    return [_anchor(p.id), heading, *_bullets(evidence)]


def _folded_line(alternatives: Sequence[Proposal]) -> str:
    """`also contained by: US-y (m 0.70, M8), …`; "merges into" if any is not a containment."""
    contained = all(_relation(a) == "containment" for a in alternatives)
    label = "also contained by" if contained else "also merges into"
    items = [f"{_kept(a)} (m {a.decisive_value:.2f}, {a.id})" for a in alternatives]
    return f"{label}: {', '.join(items)}"


def _overlap_block(p: Proposal) -> list[str]:
    what = f"overlap {p.target}: {', '.join(p.stories)}"
    heading = f"### {p.id} · {what} · mean o {p.decisive_value:.2f}{_suffix(p)}"
    return [heading, *_bullets(p.evidence)]


def _split_block(p: Proposal) -> list[str]:
    heading = f"### {p.id} · split {p.stories[0]} · bundled {p.decisive_value:.2f}{_suffix(p)}"
    return [_anchor(p.id), heading, *_bullets(p.evidence)]
