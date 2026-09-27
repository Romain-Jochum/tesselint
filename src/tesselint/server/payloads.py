"""JSON shapes of the web API and the state files (docs/ui/DESIGN.md §7.3, §7.4).

Python twins of `web/src/lib/types.ts`; field names are binding. Pure data, stdlib only.
"""

from typing import Literal, NotRequired, TypedDict

from tesselint.protocol import KeyStatus

Kind = Literal["merge", "overlap", "split"]
Action = Literal["accept", "reject", "defer"]
RelationName = Literal[
    "duplicate",
    "A_contains_B",
    "B_contains_A",
    "partial_overlap",
    "same_area_distinct",
    "unrelated",
]
Probs = dict[RelationName, float]  # TS Partial<Record<Relation, N>>
ThresholdsJson = dict[str, float]  # the eight models.Thresholds fields


# --- runs -----------------------------------------------------------------------------------------


class RunSource(TypedDict):
    path: str | None
    format: Literal["md", "json"] | None


class RunSummary(TypedDict):
    ref: str
    kind: Literal["real", "synthetic"]
    kind_inferred: bool
    dry_run: bool
    status: str
    source: RunSource
    tier: str | None
    n_stories: int
    finished_at: str | None
    cost_live_usd: float
    cost_cached_usd: float
    snapshot_sha256: str
    proposals: dict[Kind, int]


class RunsResponse(TypedDict):
    """`GET /api/runs`."""

    latest: str | None
    runs: list[RunSummary]


class StoryRow(TypedDict):
    id: str
    epic: str
    title: str
    tag: str
    words: int
    text_hash: str
    text: str
    atomic: float | None
    order: int


class Edge(TypedDict):
    a: str
    b: str
    overlap: float
    merge: float
    relation: RelationName


class EpicRef(TypedDict):
    id: str
    name: str


class TombstoneRow(TypedDict):
    id: str
    title: str
    target: str | None


class ClusterRow(TypedDict):
    id: str
    members: list[str]
    mean_overlap: float
    edges: list[Edge]


class SourceStatus(TypedDict):
    source_id: int | None
    same_snapshot: bool
    edited: list[str]
    added: list[str]
    removed: list[str]
    problems: list[str]


class RunBundle(TypedDict):
    """`GET /api/runs/{ref}`."""

    run: RunSummary
    thresholds: ThresholdsJson
    epics: list[EpicRef]
    stories: list[StoryRow]
    tombstones: list[TombstoneRow]
    clusters: list[ClusterRow]
    previous: str | None
    calibration: dict[str, float | bool] | None
    calibration_from: str | None
    source_status: SourceStatus


class MatrixPacked(TypedDict):
    """Upper triangle, row-major, i < j: k = i*n - i*(i+1)/2 + (j-i-1); `rel` indexes rel_order."""

    ids: list[str]
    o: list[float | None]
    m: list[float | None]
    rel: list[int | None]
    rel_order: list[RelationName]


# --- proposals and cases --------------------------------------------------------------------------


class MergeDetails(TypedDict):
    relation: Literal["duplicate", "containment"]
    keep: str
    drop: str
    m: float
    o: float
    fwd: Probs
    swap: Probs | None
    cluster_keep: str | None
    cluster_drop: str | None
    cos: float | None
    atomic_keep: float | None
    atomic_drop: float | None
    related: list[Edge]
    alternatives: list[str]


class OverlapDetails(TypedDict):
    cluster: str
    members: list[str]
    mean_o: float
    edges: list[Edge]
    related: list[Edge]


class SplitDetails(TypedDict):
    story: str
    bundled: float
    words: int
    n_subs: int
    cluster: str | None
    reaches: list[str]
    keeps: list[str]


class ProposalBase(TypedDict):
    id: str
    key: str
    target: str | None
    stories: list[str]
    decisive_value: float
    borderline: bool
    conflicts: list[str]


class MergeProposal(ProposalBase):
    kind: Literal["merge"]
    details: MergeDetails


class OverlapProposal(ProposalBase):
    kind: Literal["overlap"]
    details: OverlapDetails


class SplitProposal(ProposalBase):
    kind: Literal["split"]
    details: SplitDetails


ProposalPayload = MergeProposal | OverlapProposal | SplitProposal


class Case(TypedDict):
    key: str
    kind: Kind
    anchor: str
    proposals: list[str]
    strength: float
    borderline: bool
    epics: list[str]
    conflicts: list[str]
    order: int


class Hub(TypedDict):
    story: str
    keeps: list[str]
    split: str | None


class ProposalsResponse(TypedDict):
    """`GET /api/runs/{ref}/proposals`."""

    thresholds: ThresholdsJson
    proposals: list[ProposalPayload]
    cases: list[Case]
    hubs: list[Hub]


class PairDetail(TypedDict):
    """`GET /api/runs/{ref}/pairs/{a}/{b}`."""

    a: str
    b: str
    overlap: float
    merge: float
    relation: RelationName
    fwd: Probs
    swap: Probs | None
    cos: float | None
    proposals: list[str]


# --- decisions ------------------------------------------------------------------------------------


class Decision(TypedDict):
    key: str
    kind: Kind
    target: str | None
    stories: list[str]
    action: Action | None
    note: str
    text_hashes: dict[str, str]
    titles: dict[str, str]
    decisive_value: float
    run: str
    decided_at: str


class DecisionView(Decision):
    """A decision with its per-run status; computed, never stored (§3)."""

    status: Literal["current", "stale", "missing"]
    changed: list[str]
    proposed: bool
    superseded_by: str | None
    applied: bool | None
    old_texts: NotRequired[dict[str, str]]


class DecisionsResponse(TypedDict):
    """`GET /api/decisions`, `POST /api/decisions`, `POST /api/decisions/undo`."""

    rev: int
    decisions: list[DecisionView]


class DecisionChange(TypedDict):
    """One change in `POST /api/decisions`; the browser sends only keys (§7.2)."""

    key: str
    action: NotRequired[Action | Literal["clear"]]
    note: NotRequired[str]


class StoredDecision(Decision):
    """A `decisions.json` record: Decision + texts, plus the §3 decisive value and snapshot."""

    texts: dict[str, str]
    snapshot_sha256: str


class HistoryChange(TypedDict):
    key: str
    before: StoredDecision | None
    after: StoredDecision | None


class HistoryBatch(TypedDict):
    """One POST (or its undo) in `decisions.json` history (§3 History)."""

    rev: int
    at: str
    undo_of: NotRequired[int]
    changes: list[HistoryChange]


class DecisionsFile(TypedDict):
    """`<state>/decisions.json` (§7.4)."""

    format: Literal["tesselint/decisions"]
    version: Literal[1]
    rev: int
    decisions: dict[str, StoredDecision]
    history: list[HistoryBatch]


# --- diff, health, jobs ---------------------------------------------------------------------------


class EditedStory(TypedDict):
    id: str
    old_text: str
    new_text: str


class RunDiff(TypedDict):
    """`GET /api/diff?a&b`."""

    added: list[str]
    removed: list[str]
    renamed: list[tuple[str, str]]
    edited: list[EditedStory]
    new: list[str]
    gone: list[str]
    changed: list[str]
    decisions_to_review: list[str]


class ExportWritten(TypedDict):
    """`POST /api/exports` → 201: the files the server named under `<state>/exports` (additive)."""

    md_path: str
    json_path: str


class SourceInfo(TypedDict):
    id: int
    path: str
    format: Literal["md", "json"]
    exists: bool


class Health(TypedDict):
    """`GET /api/health`."""

    version: str
    build: str
    sources: list[SourceInfo]
    ui_max_usd: float


class Estimate(TypedDict):
    """`POST /api/estimates` → 201."""

    id: str
    to_ask: int
    cached: int
    usd: float
    eta_s: float
    command: str
    expires_at: str
    key: KeyStatus
    max_usd_allowed: float


class JobPhase(TypedDict):
    name: str
    done: int
    total: int


class Job(TypedDict):
    """`GET /api/jobs/{id}`, `POST /api/jobs` → 202."""

    id: str
    state: Literal["running", "done", "failed", "cancelled", "lost"]
    exit_code: int | None
    message: str | None
    run_ref: str | None
    max_usd: float
    spent_usd: float
    log_tail: list[str]
    phases: list[JobPhase]


class JobFile(Job):
    """`<state>/jobs/<id>/job.json`: a Job plus its process (§7.4)."""

    pid: int
    argv: list[str]


class ErrorDetail(TypedDict):
    code: str
    message: str


class ErrorBody(TypedDict):
    """Every API error: `{"error": {"code", "message"}}` (§7.2)."""

    error: ErrorDetail
