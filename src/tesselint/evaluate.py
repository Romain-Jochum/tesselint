"""Scoring against synthetic truth or reviewer labels (DESIGN §5, §6)."""

import json
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tesselint.errors import InputError, UsageError
from tesselint.synth import Truth
from tesselint.verdicts import PairVerdict, verdicts_from_answers

OVERLAP_KEYS = ("duplicate", "A_contains_B", "B_contains_A", "partial_overlap")
BAND_EDGES = (0.2, 0.5, 0.7)  # label-sheet strata of forward o: [0,.2) [.2,.5) [.5,.7) [.7,1]
JEV_LEVEL_O = 0.5  # §6 Jev-level recall: a planted pair Jev puts at o >= 0.5

# Label vocabularies, written into each sheet row as "options" (§5).
PAIR_LABELS = ("merge", "overlap", "none")
STORY_LABELS = {"split": True, "keep": False, "yes": True, "no": False}
PROPOSAL_LABELS = {"agree": True, "disagree": False, "yes": True, "no": False}

# §5 pass bars.
REL_MIN_AGREEMENT = 0.75
REL_MIN_KAPPA = 0.5
REL_MIN_MERGE_RECALL = 0.8
ATOMIC_AT = 0.8
ATOMIC_MIN_PRECISION = 0.6
ATOMIC_MIN_AUC = 0.7


@dataclass(frozen=True, slots=True)
class PR:
    """Precision and recall with their counts."""

    tp: int
    fp: int
    fn: int
    precision: float
    recall: float


@dataclass(frozen=True, slots=True)
class _Run:
    """The run files eval reads; `ids` in document order, verdicts averaged as in the run."""

    ids: list[str]
    stories: dict[str, dict[str, Any]]
    pairs: list[dict[str, Any]]
    proposals: list[dict[str, Any]]
    verdicts: dict[tuple[str, str], PairVerdict]

    def verdict(self, x: str, y: str) -> PairVerdict | None:
        return self.verdicts.get((x, y)) or self.verdicts.get((y, x))


def score_synthetic(run_dir: Path, truth: Truth) -> dict[str, PR]:
    """Score a run's proposals against planted truth (DESIGN §6).

    Keys: duplicates, containments, containment_direction and bundles (proposal level);
    jev_level (planted pairs at o >= 0.5; fp = unplanted pairs there); prefilter (planted pairs
    the pre-filter kept; fp = other kept pairs); container_splits (splits on planted containers,
    which are seed + addon and so fair to split: tp = split, fn = not split, never counted as
    bundle false positives).
    """
    run = _load_run(run_dir)
    merges = [p for p in run.proposals if p["kind"] == "merge"]
    split_ids = {p["stories"][0] for p in run.proposals if p["kind"] == "split" and p["stories"]}

    dup_truth = {frozenset(pair) for pair in truth.duplicates}
    cont_truth = {frozenset(pair) for pair in truth.containments}
    planted = dup_truth | cont_truth
    merged = [frozenset(p["stories"]) for p in merges]
    unplanted = sum(1 for pair in merged if pair not in planted)  # fp of every merge kind

    tp_dup = len(dup_truth & set(merged))
    tp_cont = len(cont_truth & set(merged))

    tp_dir = fp_dir = fn_dir = 0
    for container, contained in truth.containments:
        naming = [p for p in merges if frozenset(p["stories"]) == {container, contained}]
        if not naming:
            fn_dir += 1
        elif naming[0].get("target") == container:
            tp_dir += 1
        else:  # the wrong way round is both an fp and an fn
            fp_dir += 1
            fn_dir += 1

    containers = {container for container, _ in truth.containments}
    bundled = set(truth.bundled)
    tp_bun = len(split_ids & bundled)
    fp_bun = len(split_ids - bundled - containers)

    jev_hits = {frozenset((v.a, v.b)) for v in run.verdicts.values() if v.overlap >= JEV_LEVEL_O}
    kept = {frozenset((r["a"], r["b"])) for r in run.pairs if r.get("prefilter_kept") is True}
    split_containers = split_ids & containers

    return {
        "duplicates": _pr(tp_dup, unplanted, len(dup_truth) - tp_dup),
        "containments": _pr(tp_cont, unplanted, len(cont_truth) - tp_cont),
        "containment_direction": _pr(tp_dir, fp_dir + unplanted, fn_dir),
        "bundles": _pr(tp_bun, fp_bun, len(bundled) - tp_bun),
        "jev_level": _pr(len(planted & jev_hits), len(jev_hits - planted), len(planted - jev_hits)),
        "prefilter": _pr(len(planted & kept), len(kept - planted), len(planted - kept)),
        "container_splits": _pr(len(split_containers), 0, len(containers - split_containers)),
    }


def make_label_sheet(run_dir: Path, out: Path, n: int = 80, seed: int = 7) -> Path:
    """Write a blind label sheet for the run's stories and return its path (§5).

    n pairs stratified by forward o (n/4 per band), then every non-leave proposal, then every
    story. Pairs and proposals are shuffled with `seed` so row order says nothing about o or
    rank. Rows carry texts and "options" but no Jev answer; labels are blank.
    """
    if n < 0:
        raise UsageError(f"--n must be >= 0, got {n}")
    run = _load_run(run_dir)
    pos = {sid: i for i, sid in enumerate(run.ids)}

    forward: dict[frozenset[str], tuple[str, str, float]] = {}
    for r in run.pairs:
        a, b = r["a"], r["b"]
        if r.get("order") == "fwd" and a in pos and b in pos:
            o = sum(float(r["probabilities"].get(k, 0.0)) for k in OVERLAP_KEYS)
            forward.setdefault(frozenset((a, b)), (a, b, o))
    bands: list[list[tuple[str, str, float]]] = [[], [], [], []]
    for a, b, o in sorted(forward.values(), key=lambda t: sorted((pos[t[0]], pos[t[1]]))):
        bands[sum(o >= edge for edge in BAND_EDGES)].append((a, b, o))

    rng = random.Random(seed)
    sampled: list[tuple[str, str, float]] = []
    for i, band in enumerate(bands):
        quota = n // 4 + (1 if i < n % 4 else 0)
        sampled += rng.sample(band, min(quota, len(band)))
    rng.shuffle(sampled)
    proposals = [
        p
        for p in run.proposals
        if p["kind"] != "leave" and p["stories"] and all(sid in pos for sid in p["stories"])
    ]
    rng.shuffle(proposals)

    def text(sid: str) -> str:
        return str(run.stories[sid].get("text", ""))

    rows: list[dict[str, Any]] = [
        {
            "kind": "pair",
            "a": a,
            "b": b,
            "text_a": text(a),
            "text_b": text(b),
            "options": list(PAIR_LABELS),
            "label": None,
        }
        for a, b, _ in sampled
    ]
    rows += [
        {
            "kind": "proposal",
            "id": p["id"],
            "proposal_kind": p["kind"],
            "stories": p["stories"],
            "target": p.get("target"),
            "options": ["agree", "disagree"],
            "label": None,
        }
        for p in proposals
    ]
    rows += [
        {"kind": "story", "id": sid, "text": text(sid), "options": ["split", "keep"], "label": None}
        for sid in run.ids
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return out


def score_labels(run_dir: Path, labels: Path) -> dict[str, float]:
    """Score a run against a filled label sheet on the §5 bars; the dict is JSON-serialisable.

    rel.v1 (pair rows; Jev's class from the averaged verdict: merge if m >= 0.5, else overlap if
    o >= 0.5, else none): rel_n, rel_agreement, rel_kappa, rel_merge_recall, rel_pass.
    atomic.v2 (story rows with a score): atomic_n, atomic_precision (at 0.8), atomic_auc,
    atomic_pass. Proposal rows: proposal_n, proposal_agreement. A metric with nothing to count
    is left out, and a bar that cannot be measured fails; a *_pass key is present only when that
    question has labelled rows.
    """
    run = _load_run(run_dir)
    proposal_ids = {str(p["id"]) for p in run.proposals}
    pairs: list[tuple[str, str]] = []  # (Jev class, label)
    stories: list[tuple[float, bool]] = []  # (atomic score, should split)
    verdicts: list[bool] = []
    for row in _read_jsonl(labels):
        if row.get("label") is None:
            continue
        kind = row.get("kind")
        if kind == "pair":
            a, b = _known(run, row.get("a"), labels), _known(run, row.get("b"), labels)
            label = _label(row["label"], {k: k for k in PAIR_LABELS}, labels)
            pairs.append((_jev_class(run.verdict(a, b)), label))
        elif kind == "story":
            sid = _known(run, row.get("id"), labels)
            positive = _label(row["label"], STORY_LABELS, labels)
            score = run.stories[sid].get("atomic")
            if isinstance(score, int | float):
                stories.append((float(score), positive))
        elif kind == "proposal":
            if row.get("id") not in proposal_ids:
                raise InputError(f"{labels}: proposal {row.get('id')!r} is not in this run")
            verdicts.append(_label(row["label"], PROPOSAL_LABELS, labels))
        else:
            raise InputError(f"{labels}: unknown row kind {kind!r}")

    result: dict[str, float] = {"rel_n": len(pairs), **_rel_scores(pairs)}
    result |= {"atomic_n": len(stories), **_atomic_scores(stories)}
    result["proposal_n"] = len(verdicts)
    if verdicts:
        result["proposal_agreement"] = sum(verdicts) / len(verdicts)
    return result


def calibration_line(result: Mapping[str, Any]) -> str:
    """One line for proposals.md, e.g. `rel.v1 PASS · atomic.v2 FAIL (not trusted)`."""
    parts = []
    for question, key in (("rel.v1", "rel_pass"), ("atomic.v2", "atomic_pass")):
        if key not in result:
            parts.append(f"{question} not labelled")
        elif result[key]:
            parts.append(f"{question} PASS")
        else:
            parts.append(f"{question} FAIL (not trusted)")
    return " · ".join(parts)


def load_truth(path: Path) -> Truth:
    """Read truth.json (DESIGN §6); `bundled` may list ids or {"id", "parts"} objects."""
    data = _read_json(path)
    try:
        bundled = [b if isinstance(b, str) else b["id"] for b in data["bundled"]]
        parts = [tuple(b["parts"]) for b in data["bundled"] if not isinstance(b, str)]
        return Truth(
            seed=int(data["seed"]),
            n=int(data["n"]),
            duplicates=tuple(_id_pair(p) for p in data["duplicates"]),
            containments=tuple(
                _id_pair([c["container"], c["contained"]] if isinstance(c, dict) else c)
                for c in data["containments"]
            ),
            bundled=tuple(str(b) for b in bundled),
            bundle_parts=tuple(parts) if len(parts) == len(bundled) else (),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise InputError(f"{path} is not a tesselint truth file ({err!r})") from err


def _pr(tp: int, fp: int, fn: int) -> PR:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return PR(tp, fp, fn, precision, recall)


def _id_pair(value: Any) -> tuple[str, str]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError(f"expected a pair of story ids, got {value!r}")
    return str(value[0]), str(value[1])


def _jev_class(v: PairVerdict | None) -> str:
    if v is not None and v.merge >= 0.5:
        return "merge"
    if v is not None and v.overlap >= 0.5:
        return "overlap"
    return "none"


def _rel_scores(pairs: Sequence[tuple[str, str]]) -> dict[str, float]:
    """Agreement, Cohen's kappa and merge recall of Jev's class against the labels (§5)."""
    if not pairs:
        return {"rel_agreement": 0.0, "rel_kappa": 0.0}
    n = len(pairs)
    agreement = sum(j == lab for j, lab in pairs) / n
    jev, lab = Counter(j for j, _ in pairs), Counter(label for _, label in pairs)
    p_e = sum((jev[c] / n) * (lab[c] / n) for c in PAIR_LABELS)
    kappa = 1.0 if abs(1.0 - p_e) < 1e-9 else (agreement - p_e) / (1.0 - p_e)
    scores = {"rel_agreement": agreement, "rel_kappa": kappa}
    merges = [j for j, label in pairs if label == "merge"]
    recall = sum(j == "merge" for j in merges) / len(merges) if merges else None
    if recall is not None:
        scores["rel_merge_recall"] = recall
    scores["rel_pass"] = (
        agreement >= REL_MIN_AGREEMENT
        and kappa >= REL_MIN_KAPPA
        and recall is not None
        and recall >= REL_MIN_MERGE_RECALL
    )
    return scores


def _atomic_scores(stories: Sequence[tuple[float, bool]]) -> dict[str, float]:
    """Precision at 0.8 and AUC (ties count half) of atomic.v2 against the labels (§5)."""
    if not stories:
        return {}
    scores: dict[str, float] = {}
    flagged = [positive for score, positive in stories if score >= ATOMIC_AT]
    if flagged:
        scores["atomic_precision"] = sum(flagged) / len(flagged)
    pos = [score for score, positive in stories if positive]
    neg = [score for score, positive in stories if not positive]
    if pos and neg:
        wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
        scores["atomic_auc"] = wins / (len(pos) * len(neg))
    scores["atomic_pass"] = (
        scores.get("atomic_precision", 0.0) >= ATOMIC_MIN_PRECISION
        and scores.get("atomic_auc", 0.0) >= ATOMIC_MIN_AUC
    )
    return scores


def _label[T](value: Any, vocabulary: Mapping[str, T], labels: Path) -> T:
    """Map a label (case-insensitive; JSON true/false read as yes/no) through `vocabulary`."""
    if isinstance(value, bool):
        key = "yes" if value else "no"
    else:
        key = value.strip().lower() if isinstance(value, str) else ""
    if key not in vocabulary:
        raise InputError(f"{labels}: label {value!r} is not one of {', '.join(vocabulary)}")
    return vocabulary[key]


def _known(run: _Run, sid: Any, labels: Path) -> str:
    if not isinstance(sid, str) or sid not in run.stories:
        raise InputError(f"{labels}: {sid!r} is not a story of this run")
    return sid


def _load_run(run_dir: Path) -> _Run:
    """Read stories.jsonl, pairs.jsonl and proposals.json, or raise InputError (not a run)."""

    def need(name: str) -> Path:
        path = run_dir / name
        if not path.is_file():
            raise InputError(f"{run_dir} is not a run: {name} is missing")
        return path

    stories = _read_jsonl(need("stories.jsonl"))
    pairs = _read_jsonl(need("pairs.jsonl"))
    body = _read_json(need("proposals.json"))
    try:
        ids = [str(s["id"]) for s in stories]
        rel_rows = [r for r in pairs if r.get("question", "rel.v1") == "rel.v1" and r.get("b")]
        answers = [(r["a"], r["b"], r["order"], dict(r["probabilities"])) for r in rel_rows]
        proposals = list(body["proposals"])
        for p in proposals:
            p["kind"], p["stories"] = str(p["kind"]), [str(s) for s in p["stories"]]
    except (KeyError, TypeError, ValueError) as err:
        raise InputError(f"{run_dir} has malformed run files ({err!r})") from err
    return _Run(
        ids=ids,
        stories={str(s["id"]): s for s in stories},
        pairs=rel_rows,
        proposals=proposals,
        verdicts=verdicts_from_answers(ids, answers),
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise InputError(f"cannot read {path}: {err.strerror}") from err
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise InputError(f"{path} is not valid JSON ({err})") from err


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise InputError(f"cannot read {path}: {err.strerror}") from err
    except UnicodeDecodeError as err:
        raise InputError(f"{path} is not UTF-8 text") from err
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as err:
            raise InputError(f"{path}:{number}: not a JSON line ({err.msg})") from err
        if not isinstance(row, dict):
            raise InputError(f"{path}:{number}: expected a JSON object")
        rows.append(row)
    return rows
