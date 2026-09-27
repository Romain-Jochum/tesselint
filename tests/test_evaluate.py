"""evaluate: synthetic scoring and the blind label sheet (DESIGN §5, §6, §9).

Run dirs are hand-made with the §9 files eval reads: stories.jsonl, pairs.jsonl (forward answers
only, so forward o = averaged o), matrix.csv and proposals.json.

score_synthetic keys: "duplicates", "containments" (a planted pair named by a merge in either
direction), "containment_direction" (the merge must keep the container) and "bundles" (named by
a split). Per §6 "any other merge is a false positive": a merge naming no planted pair is an fp of
every merge category; a wrong-way containment is also an fp (and an fn) for containment_direction.

Label sheet (DESIGN leaves the format open; pinned here): JSON Lines, one object per row with
"kind" in {"pair", "proposal", "story"} and "label": null for the labeller to fill. Pair rows
carry "a" and "b" (story IDs), proposal and story rows carry "id". Pair labels are "merge",
"overlap" or "none"; Jev's class is merge if m >= 0.5, else overlap if o >= 0.5, else none.
score_labels returns at least "rel_agreement" and "rel_kappa" (Cohen's kappa).
"""

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from tesselint.evaluate import make_label_sheet, score_labels, score_synthetic
from tesselint.models import Thresholds
from tesselint.synth import Truth

MODEL = "typesafe/jev-1.13-20260917"
OVERLAP_KEYS = ("duplicate", "A_contains_B", "B_contains_A", "partial_overlap")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _write_run(
    run_dir: Path,
    ids: Sequence[str],
    fwd: Mapping[tuple[str, str], Mapping[str, float]],
    proposals: Sequence[Mapping[str, Any]],
    atomic: Mapping[str, float],
) -> Path:
    """Write a minimal run dir; `fwd` maps (story sent as A, story sent as B) to probabilities."""
    run_dir.mkdir(parents=True)
    _write_jsonl(
        run_dir / "stories.jsonl",
        (
            {
                "id": sid,
                "epic": "E" + sid.removeprefix("US-").split(".")[0],
                "title": f"Story {chr(65 + i)}",
                "tag": "MVP",
                "line": 10 * (i + 1),
                "words": 14,
                "text_hash": f"hash-{sid}",
                "text": f"Story {chr(65 + i)}. I tend plot {chr(65 + i)}, so that it grows well.",
                "atomic": atomic.get(sid),
                "atomic_skipped": sid not in atomic,
            }
            for i, sid in enumerate(ids)
        ),
    )
    overlap: dict[frozenset[str], float] = {}
    rows = []
    for (a, b), probs in fwd.items():
        choice = max(probs, key=lambda k: probs[k])
        overlap[frozenset((a, b))] = sum(probs.get(k, 0.0) for k in OVERLAP_KEYS)
        rows.append(
            {
                "a": a,
                "b": b,
                "order": "fwd",
                "question": "rel.v1",
                "question_hash": "qhash",
                "text_hash_a": f"hash-{a}",
                "text_hash_b": f"hash-{b}",
                "choice": choice,
                "confidence": probs[choice],
                "probabilities": dict(probs),
                "model": MODEL,
                "input_tokens": 600,
                "cost_usd": 2.5e-05,
                "latency_ms": 300,
                "cached": False,
                "gen_id": "gen-test",
                "prefilter_cos": 0.25,
                "prefilter_kept": True,
            }
        )
    _write_jsonl(run_dir / "pairs.jsonl", rows)
    matrix = ["id," + ",".join(ids)]
    for x in ids:
        cells = [overlap.get(frozenset((x, y))) if x != y else None for y in ids]
        matrix.append(",".join([x, *("" if o is None else f"{o:.3f}" for o in cells)]))
    (run_dir / "matrix.csv").write_text("\n".join(matrix) + "\n", encoding="utf-8")
    body = {"thresholds": asdict(Thresholds()), "proposals": list(proposals)}
    (run_dir / "proposals.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    return run_dir


def _merge(pid: str, kept: str, merged: str, m: float) -> dict[str, Any]:
    return {
        "id": pid,
        "kind": "merge",
        "target": kept,
        "stories": [kept, merged],
        "decisive_value": m,
        "borderline": False,
        "evidence": [f"m {m}"],
    }


def _split(pid: str, sid: str, score: float) -> dict[str, Any]:
    return {
        "id": pid,
        "kind": "split",
        "target": None,
        "stories": [sid],
        "decisive_value": score,
        "borderline": False,
        "evidence": [f"bundled {score}"],
    }


def _overlap(pid: str, cid: str, members: list[str], mean: float) -> dict[str, Any]:
    return {
        "id": pid,
        "kind": "overlap",
        "target": cid,
        "stories": members,
        "decisive_value": mean,
        "borderline": False,
        "evidence": [f"mean o {mean}"],
    }


# ---------------------------------------------------------------- score_synthetic

TRUTH = Truth(
    seed=42,
    n=12,
    duplicates=(("US-2.1", "US-1.1"), ("US-1.2", "US-3.1")),
    containments=(("US-4.1", "US-2.2"), ("US-5.1", "US-3.2"), ("US-5.2", "US-4.2")),
    bundled=("US-6.1", "US-6.2"),
)


@pytest.fixture
def synthetic_run(tmp_path: Path) -> Path:
    """M1 finds a duplicate, M2 a containment, M3 a containment the wrong way, M4 nothing."""
    ids = [f"US-{e}.{k}" for e in range(1, 7) for k in (1, 2)]
    partial = {"partial_overlap": 0.6, "same_area_distinct": 0.4}
    fwd = {
        ("US-1.1", "US-2.1"): {"duplicate": 0.8, "partial_overlap": 0.2},
        ("US-1.2", "US-3.1"): partial,
        ("US-2.2", "US-4.1"): {"B_contains_A": 0.7, "partial_overlap": 0.3},
        ("US-3.2", "US-5.1"): {"A_contains_B": 0.6, "partial_overlap": 0.4},
        ("US-4.2", "US-5.2"): partial,
        ("US-1.2", "US-4.2"): {"duplicate": 0.55, "partial_overlap": 0.45},
    }
    proposals = [
        _merge("M1", "US-1.1", "US-2.1", 0.8),
        _merge("M2", "US-4.1", "US-2.2", 0.7),
        _merge("M3", "US-3.2", "US-5.1", 0.6),
        _merge("M4", "US-1.2", "US-4.2", 0.55),
        _overlap("O1", "C1", ["US-1.2", "US-3.1"], 0.6),
        _split("S1", "US-6.1", 0.9),
        _split("S2", "US-2.1", 0.85),
    ]
    return _write_run(tmp_path / "run", ids, fwd, proposals, {sid: 0.2 for sid in ids})


@pytest.mark.parametrize(
    ("category", "tp", "fp", "fn", "precision", "recall"),
    [
        ("duplicates", 1, 1, 1, 1 / 2, 1 / 2),
        ("containments", 2, 1, 1, 2 / 3, 2 / 3),
        ("containment_direction", 1, 2, 2, 1 / 3, 1 / 3),
        ("bundles", 1, 1, 1, 1 / 2, 1 / 2),
    ],
)
def test_score_synthetic_counts_planted_hits(
    synthetic_run: Path,
    category: str,
    tp: int,
    fp: int,
    fn: int,
    precision: float,
    recall: float,
) -> None:
    """§6 metrics: merges name planted pairs (direction scored apart), splits name bundles."""
    scores = score_synthetic(synthetic_run, TRUTH)

    result = scores[category]
    assert (result.tp, result.fp, result.fn) == (tp, fp, fn)
    assert result.precision == pytest.approx(precision)
    assert result.recall == pytest.approx(recall)


# ---------------------------------------------------------------- label sheet

LABEL_IDS = [f"US-1.{k}" for k in range(1, 9)]  # 8 stories, 28 pairs
BAND_O = (
    [0.013, 0.027, 0.041, 0.058, 0.066, 0.079, 0.093, 0.117, 0.139, 0.183],  # [0, .2)
    [0.2, 0.231, 0.262, 0.297, 0.334, 0.371, 0.418, 0.463],  # [.2, .5)
    [0.5, 0.537, 0.582, 0.619, 0.664],  # [.5, .7)
    [0.7, 0.745, 0.812, 0.889, 0.951],  # [.7, 1]
)
LABEL_O: dict[frozenset[str], float] = dict(
    zip(
        (frozenset(pair) for pair in combinations(LABEL_IDS, 2)),
        (o for band in BAND_O for o in band),
        strict=True,
    )
)
ATOMIC = dict(zip(LABEL_IDS, (0.314, 0.628, 0.271, 0.853, 0.442, 0.719, 0.166, 0.587), strict=True))
FORBIDDEN_KEYS = {
    "probabilities",
    "probs",
    "choice",
    "confidence",
    "overlap",
    "merge",
    "o",
    "m",
    "relation",
    "atomic",
    "decisive_value",
    "borderline",
    "evidence",
    "fwd",
    "swap",
}


def _band(o: float) -> int:
    return sum(o >= edge for edge in (0.2, 0.5, 0.7))


@pytest.fixture
def label_run(tmp_path: Path) -> Path:
    """28 forward pairs (10/8/5/5 per band; every third sent later-first) and 3 proposals."""
    fwd: dict[tuple[str, str], dict[str, float]] = {}
    for i, (a, b) in enumerate(combinations(LABEL_IDS, 2)):
        o = LABEL_O[frozenset((a, b))]
        sent = (b, a) if i % 3 == 2 else (a, b)
        fwd[sent] = {"partial_overlap": o, "unrelated": round(1.0 - o, 3)}
    proposals = [
        _merge("M1", "US-1.1", "US-1.2", 0.537),
        _overlap("O1", "C1", ["US-1.3", "US-1.4", "US-1.5"], 0.582),
        _split("S1", "US-1.4", 0.853),
    ]
    return _write_run(tmp_path / "run", LABEL_IDS, fwd, proposals, ATOMIC)


def test_label_sheet_stratifies_pairs_by_forward_overlap(label_run: Path, tmp_path: Path) -> None:
    """§5: n pairs, n/4 from each forward-o band [0,.2), [.2,.5), [.5,.7), [.7,1], no repeats."""
    sheet = make_label_sheet(label_run, tmp_path / "labels.jsonl", n=8, seed=7)

    pairs = [frozenset((r["a"], r["b"])) for r in _read_jsonl(sheet) if r["kind"] == "pair"]
    assert len(pairs) == len(set(pairs)) == 8
    assert Counter(_band(LABEL_O[pair]) for pair in pairs) == {0: 2, 1: 2, 2: 2, 3: 2}


def test_label_sheet_covers_every_proposal_and_story_unlabelled(
    label_run: Path, tmp_path: Path
) -> None:
    """§5: the sheet adds every proposal and every story, with texts, and every label blank."""
    sheet = make_label_sheet(label_run, tmp_path / "labels.jsonl", n=8, seed=7)

    rows = _read_jsonl(sheet)
    assert {r["kind"] for r in rows} == {"pair", "proposal", "story"}
    assert {r["id"] for r in rows if r["kind"] == "proposal"} == {"M1", "O1", "S1"}
    assert sorted(r["id"] for r in rows if r["kind"] == "story") == LABEL_IDS
    assert all(r["label"] is None for r in rows)
    text = sheet.read_text(encoding="utf-8")
    for story in _read_jsonl(label_run / "stories.jsonl"):
        assert story["text"] in text


def test_label_sheet_is_blind(label_run: Path, tmp_path: Path) -> None:
    """§5: a blind sheet carries no Jev answer: no probabilities, o, m, choices or atomic scores."""
    sheet = make_label_sheet(label_run, tmp_path / "labels.jsonl", n=8, seed=7)

    rows = _read_jsonl(sheet)
    keys = {key for row in rows for key in row}
    assert keys & FORBIDDEN_KEYS == set()
    text = sheet.read_text(encoding="utf-8")
    answers = [*LABEL_O.values(), *ATOMIC.values()]
    leaked = [value for value in answers if len(repr(value)) == 5 and repr(value) in text]
    assert leaked == []


def test_label_sheet_is_deterministic_by_seed(label_run: Path, tmp_path: Path) -> None:
    """§5: the same seed writes the same bytes; another seed draws other pairs."""
    first = make_label_sheet(label_run, tmp_path / "a.jsonl", n=8, seed=7).read_bytes()
    again = make_label_sheet(label_run, tmp_path / "b.jsonl", n=8, seed=7).read_bytes()
    other = make_label_sheet(label_run, tmp_path / "c.jsonl", n=8, seed=8).read_bytes()

    assert first == again
    assert first != other


def test_score_labels_agreement_and_kappa(tmp_path: Path) -> None:
    """§5 rel.v1 pass bars: agreement and Cohen's kappa of merge/overlap/none against Jev."""
    ids = [f"US-1.{k}" for k in range(1, 6)]
    merge = {"A_contains_B": 0.7, "partial_overlap": 0.2, "same_area_distinct": 0.1}
    overlap = {"partial_overlap": 0.8, "same_area_distinct": 0.2}
    none = {"same_area_distinct": 0.7, "unrelated": 0.3}
    pairs = list(combinations(ids, 2))
    jev = [merge] * 3 + [overlap] * 3 + [none] * 4
    labels = ["merge", "merge", "overlap"] + ["overlap", "overlap", "none"] + ["none"] * 4
    fwd = {
        ((b, a) if i % 2 else (a, b)): probs
        for i, ((a, b), probs) in enumerate(zip(pairs, jev, strict=True))
    }
    run_dir = _write_run(tmp_path / "run", ids, fwd, [], {})
    sheet = tmp_path / "labels.jsonl"
    _write_jsonl(
        sheet,
        (
            {"kind": "pair", "a": a, "b": b, "label": label}
            for (a, b), label in zip(pairs, labels, strict=True)
        ),
    )
    # 8/10 agree. Jev marginals .3/.3/.4, labeller .2/.3/.5: p_e = .06 + .09 + .20 = .35,
    # kappa = (.8 - .35) / (1 - .35) = 9/13.

    scores = score_labels(run_dir, sheet)

    assert scores["rel_agreement"] == pytest.approx(0.8)
    assert scores["rel_kappa"] == pytest.approx(9 / 13)
