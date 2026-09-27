"""Build the web UI fixture (docs/ui/DESIGN.md §6; contract: tests/fixtures/ui/MANIFEST.md).

    uv run python tests/fixtures/ui/build_fixture.py [--out DIR]

Offline and deterministic: no Jev, no network, no .env. The steps of DESIGN §6:

1. `tesselint synth --n 24 --seed 42 --format json`, with `generated_at` and `source.path` fixed;
2. `pipeline.run` on FakeJev with a graded oracle into `runs/mini/20260101-090000` (run 1);
3. edit 2 stories, add 1 and remove 1 (a tombstone), then run into `runs/mini/20260102-090000`;
4. write `source.v1.json` (one edit further), `decisions.seed.json` and `manifest.json`;
5. normalise the volatile fields (timestamps, wall time, latencies, gen ids, the source path);
6. assert a group with >= 2 options, a hub with a split conflict, a borderline merge, >= 2
   overlaps, >= 1 stale and >= 1 missing seed decision, and fail loudly otherwise.

The oracle is text-hashed: each probability comes from sha256(SALT, question, texts), so an edited
story gets new answers while unchanged pairs keep their cached ones. A planted pair (the synth
truth plus FIXTURE_PLANTS) puts 0.55-0.97 on its relation, graded so that five known cases lead
the Review queue; every other pair gets o in 0-0.6 with m <= o/2, and every other story an atomic
score in 0-0.6, so no background answer can make a merge, a split or a cluster at 0.7.

The expectations in manifest.json come from the run files and the DESIGN rules, not from the code
under test: proposals from `propose()` on the saved answers, keys per UI DESIGN §3, cases per
§4.3, the diff per §4.3 and seed decision statuses per §3. Where the DESIGN leaves an order open,
lists are sorted, and the builder asserts that no tie could be broken differently. Case order is
strength desc, then the anchor's document order. Each run's `first_cases` are its first five
cases in the default Review view with decisions.seed.json loaded: `sort=strength` and
`status=open` (undecided or stale; accept, reject and defer decide a case, an accepted option its
whole group). The seed decides none of the five strongest, so the list is the same without it.
The `pair` is the strongest merge's; `neighbours_of` is the hub's; `epic_cell` is the off-diagonal
cell with the most pairs at o >= t; `diff.decisions_to_review` are the seed decisions that name an
edited, removed or renamed story.

It uses `synth --format json`, v1 sources and the new run.json fields, so it runs once those are
implemented; rebuilding then writes the same bytes (a slow test in tests/test_rundiff.py). The
model2vec model must be in the local HF cache (AGENTS.md), since `run` computes the similarity.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # model2vec from the local cache, never the network
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ["PYTHON_DOTENV_DISABLED"] = "1"  # `tesselint synth` must not read .env

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tesselint import cli  # noqa: E402
from tesselint.cluster import average_linkage  # noqa: E402
from tesselint.config import CALIBRATED_THRESHOLDS, DEFAULT_MODEL, RunConfig  # noqa: E402
from tesselint.evaluate import load_truth  # noqa: E402
from tesselint.inputv1 import validate_v1  # noqa: E402
from tesselint.jev import JevClient  # noqa: E402
from tesselint.models import ProposalKind, Story, Thresholds  # noqa: E402
from tesselint.pipeline import run as run_pipeline  # noqa: E402
from tesselint.proposals import HUB_MIN, Proposal, propose  # noqa: E402
from tesselint.render import render, text_hash  # noqa: E402
from tesselint.synth import Truth  # noqa: E402
from tesselint.verdicts import PairVerdict, overlap_matrix, verdicts_from_answers  # noqa: E402
from tests.fakes import FakeJev, State  # noqa: E402

Doc = dict[str, Any]
Grade = Literal["strong", "normal", "weak", "overlap"]
PlantRelation = Literal["duplicate", "contains", "partial"]

UI_REL = "tests/fixtures/ui"
DEFAULT_OUT = ROOT / UI_REL
SOURCE_REL = f"{UI_REL}/source.v1.json"  # run.json source.path: the file the runs came from
SEED_REL = f"{UI_REL}/decisions.seed.json"
RUN_1, RUN_2 = "mini/20260101-090000", "mini/20260102-090000"
REF_1, REF_2 = RUN_1.replace("/", "~"), RUN_2.replace("/", "~")
STARTED_AT = {RUN_1: "2026-01-01T09:00:00+00:00", RUN_2: "2026-01-02T09:00:00+00:00"}
GENERATED_AT = "2026-01-01T08:00:00Z"
DECIDED_AT = "2026-01-01T10:00:00+00:00"
SYNTH_PATH = "synth/stories.md"
INPUT_NAME = "source.v1.json"  # the temporary input every run reads, rewritten between runs

N, SEED = 24, 42
SALT = "story-sim ui fixture 2"  # chosen so every tie guard below holds
THRESHOLDS = CALIBRATED_THRESHOLDS  # what `tesselint run` uses (cluster 0.7)
NEIGHBOUR_MIN_O = 0.4  # the Story view lists neighbours with o >= 0.4 (UI DESIGN §2)
FIRST_CASES = 5
CHANGED_MIN_DELTA = 0.05  # UI DESIGN §4.3
TIE_GAP = 1e-6  # values closer than this count as a tie an implementation could order either way

GRADES: Mapping[Grade, tuple[float, float]] = {
    "strong": (0.85, 0.97),  # the five cases that lead the Review queue
    "normal": (0.55, 0.84),
    "weak": (0.55, 0.59),  # m within 0.1 of the 0.5 merge threshold: borderline
    "overlap": (0.72, 0.84),  # a partial overlap: a cluster at 0.7 that no merge covers
}
BACKGROUND_MAX = 0.6  # "0-0.6 otherwise" (DESIGN §6)


@dataclass(frozen=True, slots=True)
class Plant:
    """A planted pair: duplicates, `x` contains `y`, or a partial overlap."""

    x: str
    y: str
    relation: PlantRelation
    grade: Grade


HUB = "US-6.2"  # a planted bundle (split) that contains three stories: a hub with a split conflict
FIXTURE_PLANTS = (
    Plant(HUB, "US-6.3", "contains", "strong"),
    Plant(HUB, "US-5.1", "contains", "strong"),
    Plant(HUB, "US-4.2", "contains", "strong"),
    Plant("US-3.4", "US-3.1", "contains", "normal"),  # US-4.1 contains US-3.1 too: two options
    Plant("US-7.5", "US-8.99", "duplicate", "weak"),
    Plant("US-1.2", "US-4.3", "partial", "overlap"),
    Plant("US-2.2", "US-1.3", "partial", "overlap"),
    Plant("US-7.2", "US-7.6", "contains", "normal"),  # US-7.6 is added in run 2: a new merge
)
STRONG_TRUTH = frozenset({frozenset({"US-4.1", "US-3.1"})})  # the two-option group's option 1
ATOMIC_GRADES: Mapping[str, Grade] = {HUB: "strong"}  # the other planted bundles are "normal"

# Run 2: two edits, one addition, one removal. The source is one edit further.
EDITS_RUN_2 = {
    "US-8.1": " I also get the receipt by email.",
    "US-1.2": " The offer also names the nearest water tap.",
}
REMOVED_RUN_2 = ("US-2.1", "US-3.2", "the wish-list story covers donations")  # id, into, reason
ADDED_AFTER = "US-7.5"
ADDED_RUN_2: Doc = {
    "id": "US-7.6",
    "epic": "E7",
    "title": "Upload concession proof",
    "tag": "Lite",
    "statement": (
        "I upload a photo of my pension or benefits letter from my phone, so that the "
        "treasurer can check my concession."
    ),
    "details": ["The upload takes a photo or a PDF."],
    "status": "live",
}
EDITS_SOURCE = {"US-6.1": " A reminder comes a month before renewal."}

# Decisions made on run 1 (key, action, note); none is among the first cases of either run.
SEEDS: tuple[tuple[str, Literal["accept", "reject", "defer"], str], ...] = (
    ("merge:US-3.2>US-2.1", "accept", "Fold the donation flow into the wish-list story."),
    ("merge:US-7.1>US-8.1", "accept", ""),
    ("overlap:US-1.2+US-4.3", "reject", "Different flows; keep both."),
    ("merge:US-7.2>US-8.3", "defer", "Check the concession wording first."),
    ("overlap:US-1.3+US-2.2", "accept", ""),
)


class FixtureError(RuntimeError):
    """A DESIGN §6 property or a determinism guard does not hold."""


def ensure(condition: bool, message: str) -> None:
    """Fail loudly (never stripped by -O, unlike assert)."""
    if not condition:
        raise FixtureError(message)


# --- documents ------------------------------------------------------------------------------------


def write_doc(path: Path, doc: Mapping[str, Any]) -> None:
    """One canonical JSON form, so rebuilt files and their snapshot hashes are identical."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_input(path: Path, doc: Mapping[str, Any]) -> None:
    """The source a run reads."""
    write_doc(path, doc)


def validate(doc: Mapping[str, Any]) -> None:
    """The builder's own edits must keep the document valid (INPUT.md §2)."""
    errors = [p for p in validate_v1(doc) if p.severity == "error"]
    ensure(not errors, f"invalid fixture document: {errors}")


def synth_doc(tmp: Path) -> tuple[Doc, Truth]:
    """Step 1: `tesselint synth --format json`, canonicalised; the Truth from its truth.json."""
    out = tmp / "synth"
    argv = ["synth", "--out", str(out), "--n", str(N), "--seed", str(SEED), "--format", "json"]
    ensure(cli.main(argv) == 0, "tesselint synth --format json failed")
    doc: Doc = json.loads((out / "stories.json").read_text("utf-8"))
    ensure(doc.get("x-synthetic") is True, "synth --format json must set x-synthetic")
    doc["generated_at"] = GENERATED_AT
    doc["source"] = {**doc["source"], "path": SYNTH_PATH}
    return doc, load_truth(out / "truth.json")


def story_row(doc: Mapping[str, Any], sid: str) -> Doc:
    row: Doc = next(s for s in doc["stories"] if s["id"] == sid)
    return row


def edited(doc: Mapping[str, Any], edits: Mapping[str, str]) -> Doc:
    """A copy with `suffix` appended to each story's statement."""
    new = copy.deepcopy(dict(doc))
    for sid, suffix in edits.items():
        row = story_row(new, sid)
        ensure(bool(row["statement"]), f"{sid} needs a statement to edit")
        row["statement"] += suffix
    return new


def next_version(doc: Mapping[str, Any]) -> Doc:
    """Step 3: edit two stories, add one, turn one into a tombstone (INPUT.md §2 Story)."""
    new = edited(doc, EDITS_RUN_2)
    sid, into, reason = REMOVED_RUN_2
    stories: list[Doc] = new["stories"]
    at = next(i for i, s in enumerate(stories) if s["id"] == sid)
    old = stories[at]
    tomb: Doc = {"id": sid, "epic": old["epic"], "title": old["title"]}
    tomb |= {k: old[k] for k in ("tag", "priority") if k in old}
    tomb |= {"status": "removed", "merged_into": into, "removed_reason": reason}
    tomb |= {"source_line": old["source_line"]} if "source_line" in old else {}
    stories[at] = tomb
    after = next(i for i, s in enumerate(stories) if s["id"] == ADDED_AFTER)
    stories.insert(after + 1, dict(ADDED_RUN_2))
    return new


def live_stories(doc: Mapping[str, Any]) -> tuple[Story, ...]:
    """INPUT.md §4 mapping: statement -> core, details -> subs, tag or "", source_line -> line."""
    return tuple(
        Story(
            id=s["id"],
            epic=s["epic"],
            title=s["title"],
            tag=s.get("tag") or "",
            core=s["statement"],
            subs=tuple(s.get("details", ())),
            line=s.get("source_line", 0),
        )
        for s in doc["stories"]
        if s.get("status", "live") == "live"
    )


def texts_of(doc: Mapping[str, Any]) -> dict[str, str]:
    """Live story ID -> the full text sent to Jev (INPUT.md §3, md-clean/1)."""
    return {s.id: render(s) for s in live_stories(doc)}


# --- oracle ---------------------------------------------------------------------------------------


def unit(*parts: str) -> float:
    """A uniform number in [0, 1) from the parts' sha256: the same texts, the same answer."""
    digest = hashlib.sha256("\x1f".join((SALT, *parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def graded(grade: Grade, u: float) -> float:
    lo, hi = GRADES[grade]
    return lo + (hi - lo) * u


class Oracle:
    """FakeJev answer function: graded probabilities for planted pairs, low ones elsewhere."""

    def __init__(self, truth: Truth, docs: Iterable[Mapping[str, Any]]) -> None:
        plants = [
            Plant(a, b, "duplicate", "strong" if {a, b} in STRONG_TRUTH else "normal")
            for a, b in truth.duplicates
        ]
        plants += [
            Plant(c, d, "contains", "strong" if {c, d} in STRONG_TRUTH else "normal")
            for c, d in truth.containments
        ]
        plants += FIXTURE_PLANTS
        self.plants: dict[frozenset[str], Plant] = {}
        for p in plants:
            ensure(frozenset((p.x, p.y)) not in self.plants, f"pair planted twice: {p}")
            self.plants[frozenset((p.x, p.y))] = p
        self.atomic: dict[str, Grade] = {sid: "normal" for sid in truth.bundled}
        self.atomic.update(ATOMIC_GRADES)
        self.ids: dict[str, str] = {}
        for doc in docs:
            for sid, text in texts_of(doc).items():
                ensure(
                    self.ids.setdefault(text, sid) == sid, f"{sid} renders like {self.ids[text]}"
                )

    def answer(self, state: State, question_id: str) -> Mapping[str, float]:
        if isinstance(state, str):
            grade = self.atomic.get(self.ids[state])
            u = unit(question_id, state)
            return {"yes": graded(grade, u) if grade else BACKGROUND_MAX * u**2}
        a_text, b_text = state["A"], state["B"]
        a, b = self.ids[a_text], self.ids[b_text]
        u1, u2, u3 = (unit(question_id, a_text, b_text, part) for part in "123")
        plant = self.plants.get(frozenset((a, b)))
        if plant is None:
            o = BACKGROUND_MAX * u1**3
            m = o * u2 / 2
            same_area = (1.0 - o) * 0.4 * u3
            return {
                "duplicate": m * u3,
                "A_contains_B": m * (1.0 - u3),
                "partial_overlap": o - m,
                "same_area_distinct": same_area,
                "unrelated": 1.0 - o - same_area,
            }
        main = graded(plant.grade, u1)
        rest = 1.0 - main
        if plant.relation == "partial":
            return {
                "partial_overlap": main,
                "same_area_distinct": rest * u2,
                "unrelated": rest * (1.0 - u2),
            }
        if plant.relation == "duplicate":
            relation = "duplicate"
        else:
            relation = "A_contains_B" if a == plant.x else "B_contains_A"
        return {relation: main, "partial_overlap": rest * u2, "same_area_distinct": rest * (1 - u2)}


# --- runs -----------------------------------------------------------------------------------------


def run_config(source: Path, out_root: Path, cache_dir: Path) -> RunConfig:
    """Full matrix, swap on, one request at a time (so answers arrive in a fixed order)."""
    return RunConfig(
        source=source,
        out_root=out_root,
        epics=None,
        render="full",
        prefilter=False,
        prefilter_k=10,
        swap=True,
        concurrency=1,
        rate_per_s=10_000.0,
        max_usd=1.0,
        cache_dir=cache_dir,
        model=DEFAULT_MODEL,
        base_url="https://jev.fixture.invalid/api",
        resume=None,
        refresh=False,
        dry_run=False,
        thresholds=THRESHOLDS,
        tier=None,
    )


async def ask(cfg: RunConfig, fake: FakeJev) -> Path:
    client = JevClient(
        "sk-or-fixture", base_url=cfg.base_url, model=cfg.model, transport=fake.transport
    )
    try:
        return await run_pipeline(cfg, client)
    finally:
        await client.aclose()


def run_version(doc: Doc, oracle: Oracle, tmp: Path, target: Path, name: str) -> None:
    """Run `doc` on FakeJev (sharing the answer cache) and move the run dir to `target`."""
    source = tmp / "inputs" / INPUT_NAME
    write_input(source, doc)
    cfg = run_config(source, tmp / "runs" / name, tmp / "cache")
    run_dir = asyncio.run(ask(cfg, FakeJev(oracle.answer)))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(target))


def check_new_fields(meta: Mapping[str, Any]) -> None:
    """UI DESIGN §4.2: source, kind, dry_run and input are in run.json."""
    ensure(meta.get("kind") == "synthetic", f"run.json kind: {meta.get('kind')!r}")
    ensure(meta.get("dry_run") is False, f"run.json dry_run: {meta.get('dry_run')!r}")
    source = meta.get("source")
    ensure(isinstance(source, dict) and source.get("format") == "json", f"source: {source!r}")
    ensure(isinstance(meta.get("input"), dict), f"run.json input: {meta.get('input')!r}")


def rounded(value: Any) -> Any:
    """Floats rounded to 12 places, so summation order cannot show in the last bits."""
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    return value


def normalise(run_dir: Path, started_at: str) -> None:
    """Step 5: fixed timestamps and ids, zero wall time and latencies, the fixture source path."""
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text("utf-8"))
    check_new_fields(meta)
    old_id = meta["run_id"]
    meta.update(
        run_id=run_dir.name,
        started_at=started_at,
        finished_at=started_at,
        wall_s=0.0,
        calls_per_s=0.0,
        latency_ms={"p50": 0.0, "p95": 0.0},
        cost_usd=rounded(meta["cost_usd"]),
        tokens=rounded(meta["tokens"]),
    )
    meta["source"] = {**meta["source"], "path": SOURCE_REL}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    pairs_path = run_dir / "pairs.jsonl"
    rows = [json.loads(line) for line in pairs_path.read_text("utf-8").splitlines() if line]
    rows.sort(key=lambda r: (r["order"], r["a"], r["b"]))
    for n, row in enumerate(rows, start=1):
        row["latency_ms"] = 0.0
        if row.get("prefilter_cos") is not None:
            row["prefilter_cos"] = round(row["prefilter_cos"], 6)
        if row["gen_id"] != "local":
            row["gen_id"] = f"gen-fixture-{n}"
    pairs_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    md_path = run_dir / "proposals.md"
    md = md_path.read_text("utf-8")
    ensure(md.count(f"run {old_id}") == 1, "proposals.md must name its run once")
    md_path.write_text(md.replace(f"run {old_id}", f"run {run_dir.name}"), encoding="utf-8")


# --- expectations ---------------------------------------------------------------------------------


def key_of(p: Proposal) -> str:
    """UI DESIGN §3 Keys."""
    if p.kind is ProposalKind.MERGE:
        return f"merge:{p.stories[0]}>{p.stories[1]}"
    if p.kind is ProposalKind.SPLIT:
        return f"split:{p.stories[0]}"
    return "overlap:" + "+".join(sorted(p.stories))


@dataclass(frozen=True, slots=True)
class Case:
    """UI DESIGN §4.3: a merge group per dropped story, a case per split or overlap."""

    key: str
    kind: str
    strength: float
    order: int
    options: tuple[str, ...]  # proposal keys; a merge group's strongest first


@dataclass(frozen=True)
class RunFacts:
    """One run as the UI derives it at the run's thresholds."""

    ref: str
    doc: Doc
    meta: dict[str, Any]
    stories: tuple[Story, ...]
    verdicts: dict[tuple[str, str], PairVerdict]
    atomic: dict[str, float | None]

    def proposals(self, th: Thresholds = THRESHOLDS) -> list[Proposal]:
        ids = [s.id for s in self.stories]
        clusters = average_linkage(ids, overlap_matrix(ids, self.verdicts), threshold=th.cluster)
        found = propose(self.stories, self.verdicts, clusters, self.atomic, th)
        return [p for p in found if p.kind is not ProposalKind.LEAVE]

    def verdict(self, a: str, b: str) -> PairVerdict | None:
        return self.verdicts.get((a, b)) or self.verdicts.get((b, a))

    @property
    def order(self) -> dict[str, int]:
        return {s.id: i for i, s in enumerate(self.stories)}


def read_run(ref: str, run_dir: Path, doc: Doc) -> RunFacts:
    """Rebuild a run from its saved answers; check it matches what the run wrote."""
    meta = json.loads((run_dir / "run.json").read_text("utf-8"))
    story_rows = [
        json.loads(x) for x in (run_dir / "stories.jsonl").read_text("utf-8").splitlines()
    ]
    pair_rows = [json.loads(x) for x in (run_dir / "pairs.jsonl").read_text("utf-8").splitlines()]
    stories = live_stories(doc)
    ensure([r["id"] for r in story_rows] == [s.id for s in stories], f"{ref}: stories differ")
    ensure(meta["thresholds"] == asdict(THRESHOLDS), f"{ref}: thresholds differ")
    answers = ((r["a"], r["b"], r["order"], r["probabilities"]) for r in pair_rows)
    verdicts = verdicts_from_answers([s.id for s in stories], answers)
    facts = RunFacts(ref, doc, meta, stories, verdicts, {r["id"]: r["atomic"] for r in story_rows})
    written = json.loads((run_dir / "proposals.json").read_text("utf-8"))["proposals"]
    shape = [
        (w["id"], w["kind"], w["target"], w["stories"], w["decisive_value"], w["borderline"])
        for w in written
        if w["kind"] != "leave"
    ]
    mine = [
        (p.id, p.kind.value, p.target, list(p.stories), p.decisive_value, p.borderline)
        for p in facts.proposals()
    ]
    ensure(shape == mine, f"{ref}: re-derived proposals differ from proposals.json")
    return facts


def no_ties(values: Sequence[float], what: str) -> None:
    ensure(all(a - b > TIE_GAP for a, b in pairwise(values)), f"tie in {what}: {list(values)}")


def cases_of(f: RunFacts) -> list[Case]:
    """Cases in default Review order: strength desc, then document order, then key."""
    order = f.order
    groups: dict[str, list[Proposal]] = {}
    proposals = f.proposals()
    for p in proposals:
        if p.kind is ProposalKind.MERGE:
            groups.setdefault(p.stories[1], []).append(p)
    cases: list[Case] = []
    for drop, merges in groups.items():
        merges = sorted(merges, key=lambda p: -p.decisive_value)
        options = tuple(key_of(p) for p in merges)
        top = merges[0].decisive_value
        cases.append(Case(f"mg:{drop}", "merge", top, order[drop], options))
    for p in proposals:
        if p.kind in (ProposalKind.SPLIT, ProposalKind.OVERLAP):
            anchor = min(order[s] for s in p.stories)
            cases.append(Case(key_of(p), p.kind.value, p.decisive_value, anchor, (key_of(p),)))
    cases.sort(key=lambda c: (-c.strength, c.order, c.key))
    no_ties([c.strength for c in cases[: FIRST_CASES + 1]], f"{f.ref} first case strengths")
    return cases


def open_cases(
    cases: Sequence[Case], records: Mapping[str, Mapping[str, Any]], f: RunFacts
) -> list[Case]:
    """The default Review filter with the seed loaded: `status=open`, undecided or stale (§2).

    A decision decides its case while it is current on this run: accept, reject and defer all
    decide; an accepted option decides its whole merge group, which otherwise needs every option
    decided. A stale decision reopens its case; a missing one has no case left.
    """
    status = decision_status(records, f)
    current = {k for k, r in records.items() if r["action"] is not None and status[k] == "current"}
    accepted = {k for k in current if records[k]["action"] == "accept"}
    stale = {k for k, st in status.items() if st == "stale"}

    def is_open(case: Case) -> bool:
        if stale & set(case.options):
            return True
        return not (accepted & set(case.options) or set(case.options) <= current)

    return [c for c in cases if is_open(c)]


def neighbours(f: RunFacts, sid: str) -> list[str]:
    """Stories with o >= 0.4 to `sid`, by o desc (the Story view's list), the first five."""
    near = []
    for other in f.stories:
        v = f.verdict(sid, other.id) if other.id != sid else None
        if v is not None and v.overlap >= NEIGHBOUR_MIN_O:
            near.append((v.overlap, f.order[other.id], other.id))
    near.sort(key=lambda x: (-x[0], x[1]))
    no_ties([o for o, _, _ in near[:6]], f"{f.ref} neighbours of {sid}")
    return [sid for _, _, sid in near[:5]]


def epic_cell(f: RunFacts) -> dict[str, Any]:
    """The off-diagonal epic cell with the most pairs at o >= t (first in epic order on a tie)."""
    t = THRESHOLDS.cluster
    epics = [e["id"] for e in f.doc["epics"]]
    members = {e: [s.id for s in f.stories if s.epic == e] for e in epics}
    best: dict[str, Any] | None = None
    for i, row in enumerate(epics):
        for col in epics[i + 1 :]:
            edges = [f.verdict(a, b) for a in members[row] for b in members[col]]
            ensure(all(v is not None for v in edges), f"{f.ref}: unanswered pair in {row}x{col}")
            above = sum(v is not None and v.overlap >= t for v in edges)
            if best is None or above > best["pairs_at_or_above_t"]:
                cell = {"row": row, "col": col, "pairs": len(edges), "pairs_at_or_above_t": above}
                best = cell | {"t": t}
    ensure(best is not None and best["pairs_at_or_above_t"] > 0, f"{f.ref}: no epic cell above t")
    assert best is not None
    return best


def run_entry(f: RunFacts, records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """One `runs[ref]` entry of the manifest (MANIFEST.md)."""
    proposals = f.proposals()
    by_kind = Counter(p.kind.value for p in proposals)
    merges = [p for p in proposals if p.kind is ProposalKind.MERGE]
    cases = cases_of(f)
    shown = open_cases(cases, records, f)
    no_ties([c.strength for c in shown[: FIRST_CASES + 1]], f"{f.ref} open case strengths")
    first = [c.key for c in shown[:FIRST_CASES]]
    # The seed decides no case among the strongest five, so the list reads the same without it.
    ensure(first == [c.key for c in cases[:FIRST_CASES]], f"{f.ref}: the seed hides a first case")

    groups = [c for c in cases if c.kind == "merge" and len(c.options) >= 2]
    ensure(bool(groups), f"{f.ref}: no merge group with >= 2 options")
    group = groups[0]
    m_of = {key_of(p): p.decisive_value for p in merges}
    no_ties([m_of[k] for k in group.options], f"{f.ref} options of {group.key}")

    kept = Counter(p.stories[0] for p in merges)
    hubs = sorted(sid for sid, n in kept.items() if n >= HUB_MIN)
    splits = {p.stories[0]: key_of(p) for p in proposals if p.kind is ProposalKind.SPLIT}
    hub_splits = [h for h in hubs if h in splits]
    ensure(bool(hub_splits), f"{f.ref}: no hub with a split conflict (hubs {hubs})")
    hub = hub_splits[0]

    borderline = [key_of(p) for p in merges if p.borderline]
    ensure(bool(borderline), f"{f.ref}: no borderline merge")
    overlaps = sorted(key_of(p) for p in proposals if p.kind is ProposalKind.OVERLAP)
    ensure(len(overlaps) >= 2, f"{f.ref}: {len(overlaps)} overlaps, want >= 2")

    top = merges[0]
    v = f.verdict(top.stories[0], top.stories[1])
    ensure(v is not None, f"{f.ref}: no verdict for {key_of(top)}")
    assert v is not None
    at_09 = f.proposals(replace(THRESHOLDS, merge=0.9))
    tombstones = sum(s.get("status") == "removed" for s in f.doc["stories"])
    ensure(f.meta["n_stories"] == len(f.stories), f"{f.ref}: n_stories")
    ensure(f.meta["n_tombstones"] == tombstones, f"{f.ref}: n_tombstones")
    return {
        "kind": "synthetic",
        "n_stories": len(f.stories),
        "n_epics": len(f.doc["epics"]),
        "n_tombstones": tombstones,
        "proposals": {k: by_kind[k] for k in ("merge", "overlap", "split")},
        "cases": {
            "total": len(cases),
            "merge_groups": sum(c.kind == "merge" for c in cases),
            "split": sum(c.kind == "split" for c in cases),
            "overlap": sum(c.kind == "overlap" for c in cases),
        },
        "first_cases": first,
        "hubs": hubs,
        "multi_option_group": group.key,
        "multi_option_group_options": list(group.options),
        "hub_story": hub,
        "hub_split_key": splits[hub],
        "borderline_merge_key": borderline[0],
        "overlap_keys": overlaps,
        "pair": {
            "a": v.a,
            "b": v.b,
            "overlap": v.overlap,
            "merge": v.merge,
            "relation": v.relation.value,
        },
        "neighbours_of": {"story": hub, "top5": neighbours(f, hub)},
        "merges_at_merge_0_9": sum(p.kind is ProposalKind.MERGE for p in at_09),
        "epic_cell": epic_cell(f),
    }


def story_changes(old: Mapping[str, str], new: Mapping[str, str]) -> dict[str, Any]:
    """Added, removed, edited and renamed (a removed text under a new ID) by rendered text."""
    gone = sorted(set(old) - set(new))
    came = sorted(set(new) - set(old))
    renamed = [[o, n] for o in gone for n in came if text_hash(old[o]) == text_hash(new[n])]
    return {
        "added": [n for n in came if n not in {r[1] for r in renamed}],
        "removed": [o for o in gone if o not in {r[0] for r in renamed}],
        "edited": [
            {"id": sid, "old_text": old[sid], "new_text": new[sid]}
            for sid in sorted(set(old) & set(new))
            if old[sid] != new[sid]
        ],
        "renamed": renamed,
    }


def diff(a: RunFacts, b: RunFacts, seeds: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """UI DESIGN §4.3 `diff_runs`, plus the seed decisions that name a changed story."""
    stories = story_changes(texts_of(a.doc), texts_of(b.doc))
    pa = {key_of(p): p for p in a.proposals()}
    pb = {key_of(p): p for p in b.proposals()}
    changed = []
    for key in sorted(set(pa) & set(pb)):
        delta = abs(pa[key].decisive_value - pb[key].decisive_value)
        ensure(abs(delta - CHANGED_MIN_DELTA) > TIE_GAP, f"{key}: |Δ| {delta} sits on 0.05")
        if delta >= CHANGED_MIN_DELTA or pa[key].borderline != pb[key].borderline:
            changed.append(key)
    touched = {e["id"] for e in stories["edited"]} | set(stories["removed"])
    touched |= {old for old, _ in stories["renamed"]}
    review = sorted(k for k, d in seeds.items() if touched & set(d["stories"]))
    return {
        "a": a.ref,
        "b": b.ref,
        **stories,
        "new": sorted(set(pb) - set(pa)),
        "gone": sorted(set(pa) - set(pb)),
        "changed": changed,
        "decisions_to_review": review,
    }


def seed_decisions(f: RunFacts) -> dict[str, dict[str, Any]]:
    """UI DESIGN §3 records for SEEDS, made on run 1 (`decisions.json` values, §7.4)."""
    by_key = {key_of(p): p for p in f.proposals()}
    texts = texts_of(f.doc)
    titles = {s.id: s.title for s in f.stories}
    records: dict[str, dict[str, Any]] = {}
    for key, action, note in SEEDS:
        ensure(key in by_key, f"seed decision {key} is not proposed on {f.ref}")
        p = by_key[key]
        records[key] = {
            "key": key,
            "kind": p.kind.value,
            "target": p.target,
            "stories": list(p.stories),
            "action": action,
            "note": note,
            "text_hashes": {sid: text_hash(texts[sid]) for sid in p.stories},
            "titles": {sid: titles[sid] for sid in p.stories},
            "run": f.ref,
            "decided_at": DECIDED_AT,
            "texts": {sid: texts[sid] for sid in p.stories},
            "decisive_value": p.decisive_value,
            "snapshot_sha256": f.meta["snapshot_sha256"],
        }
    return records


def decisions_file(records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """`decisions.json` (UI DESIGN §7.4): the seed decisions as one batch, rev 1."""
    changes = [{"key": k, "before": None, "after": r} for k, r in records.items()]
    return {
        "format": "tesselint/decisions",
        "version": 1,
        "rev": 1,
        "decisions": dict(records),
        "history": [{"rev": 1, "at": DECIDED_AT, "changes": changes}],
    }


def decision_status(records: Mapping[str, Mapping[str, Any]], f: RunFacts) -> dict[str, str]:
    """UI DESIGN §3: `missing` if a story is gone, `stale` if a text hash differs, else current."""
    hashes = {sid: text_hash(t) for sid, t in texts_of(f.doc).items()}
    status = {}
    for key, r in records.items():
        if any(sid not in hashes for sid in r["stories"]):
            status[key] = "missing"
        elif any(hashes[sid] != h for sid, h in r["text_hashes"].items()):
            status[key] = "stale"
        else:
            status[key] = "current"
    return status


def statuses(records: Mapping[str, Mapping[str, Any]], f: RunFacts) -> dict[str, Any]:
    """`seed_decisions` of the manifest: the statuses on run `f` and how many are orphaned."""
    status = decision_status(records, f)
    proposed = {key_of(p) for p in f.proposals()}
    stale = [k for k, st in status.items() if st == "stale"]
    missing = [k for k, st in status.items() if st == "missing"]
    return {
        "file": SEED_REL,
        "total": len(records),
        "on_latest": {
            "current": len(records) - len(stale) - len(missing),
            "stale": len(stale),
            "missing": len(missing),
            "orphaned": sum(k not in proposed for k in records),
        },
        "stale_keys": sorted(stale),
        "missing_keys": sorted(missing),
    }


def source_status(latest: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, list[str]]:
    """Story IDs the current source edited, added or removed since the latest run."""
    changes = story_changes(texts_of(latest), texts_of(source))
    return {
        "edited": [e["id"] for e in changes["edited"]],
        "added": changes["added"],
        "removed": changes["removed"],
    }


# --- build ----------------------------------------------------------------------------------------


def build(out: Path) -> dict[str, Any]:
    """Write the fixture under `out`; return the manifest."""
    for stale_output in (out / "runs",):
        if stale_output.exists():
            shutil.rmtree(stale_output)
    with tempfile.TemporaryDirectory(prefix="tesselint-ui-fixture-") as tmp_name:
        tmp = Path(tmp_name)
        doc1, truth = synth_doc(tmp)
        doc2 = next_version(doc1)
        doc3 = edited(doc2, EDITS_SOURCE)
        for doc in (doc1, doc2, doc3):
            validate(doc)
        oracle = Oracle(truth, (doc1, doc2, doc3))
        for rel, doc, name in ((RUN_1, doc1, "one"), (RUN_2, doc2, "two")):
            run_version(doc, oracle, tmp, out / "runs" / rel, name)
            normalise(out / "runs" / rel, STARTED_AT[rel])

    run1 = read_run(REF_1, out / "runs" / RUN_1, doc1)
    run2 = read_run(REF_2, out / "runs" / RUN_2, doc2)
    records = seed_decisions(run1)
    seeds = statuses(records, run2)
    ensure(seeds["on_latest"]["stale"] >= 1, "no stale seed decision on the latest run")
    ensure(seeds["on_latest"]["missing"] >= 1, "no missing seed decision on the latest run")
    manifest = {
        "format": "tesselint/ui-fixture-manifest",
        "version": 1,
        "runs_root": f"{UI_REL}/runs",
        "source": SOURCE_REL,
        "latest": REF_2,
        "previous": REF_1,
        "runs": {f.ref: run_entry(f, records) for f in (run1, run2)},
        "diff": diff(run1, run2, records),
        "seed_decisions": seeds,
        "source_vs_latest": source_status(doc2, doc3),
    }
    write_doc(out / "source.v1.json", doc3)
    write_doc(out / "decisions.seed.json", decisions_file(records))
    write_doc(out / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="fixture directory")
    args = parser.parse_args(argv)
    manifest = build(args.out)
    for ref, run in manifest["runs"].items():
        print(
            f"{ref}: {run['n_stories']} stories, {run['proposals']}, "
            f"{run['cases']['total']} cases, hub {run['hub_story']}"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
