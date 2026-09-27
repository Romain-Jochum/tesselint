"""Append-only answer cache (DESIGN §2.6 Incremental cache, §8 cache.py, §9 cache line)."""

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from tesselint.cache import AnswerCache, cache_key
from tesselint.jev import Answer
from tesselint.questions import ATOMIC_V2, REL_V1, Question

MODEL = "typesafe/jev-1.13"
TEXT_A = "Tool catalogue. The shed lists every tool."
TEXT_B = "Consistent branding. Every page agrees."
HASH_A = "a" * 64
HASH_B = "b" * 64

REL_ANSWER = Answer(
    question_id="rel.v1",
    choice="partial_overlap",
    confidence=0.48,
    probabilities={"partial_overlap": 0.53, "A_contains_B": 0.37, "unrelated": 0.1},
    model="typesafe/jev-1.13-20260917",
    input_tokens=1412,
    cost_usd=5.9304e-05,
    gen_id="gen-dec-1790422720-FIXTURE0000000000001",
    latency_ms=301.5,
)
ATOMIC_ANSWER = Answer(
    question_id="atomic.v2",
    choice=None,
    confidence=None,
    probabilities={"yes": 0.73},
    model="typesafe/jev-1.13-20260917",
    input_tokens=468,
    cost_usd=1.9656e-05,
    gen_id="gen-dec-0000000000-FIXTURE0000000000005",
    latency_ms=280.0,
)


def _cache_file(path: Path) -> Path:
    # DESIGN §2.6 stores `answers.jsonl`; accept `path` as that file or as its directory.
    return path if path.is_file() else path / "answers.jsonl"


def _reworded_rel_v1() -> Question:
    payload: dict[str, Any] = {**REL_V1.payload, "instructions": "How do A and B relate?"}
    return Question(REL_V1.id, payload)


# --- cache_key ------------------------------------------------------------------------------------


def test_cache_key_is_deterministic_hex() -> None:
    """DESIGN §2.6 Key: sha256 of canonical JSON, so the same inputs give the same key."""
    key = cache_key(REL_V1, MODEL, TEXT_A, TEXT_B)

    assert key == cache_key(REL_V1, MODEL, TEXT_A, TEXT_B)
    assert len(key) == 64
    assert int(key, 16) >= 0


@pytest.mark.parametrize(
    "changed",
    [
        {"question": _reworded_rel_v1()},
        {"model": "typesafe/jev-1.14"},
        {"text_a": TEXT_A + " Edited."},
        {"text_b": TEXT_B + " Edited."},
        {"text_a": TEXT_B, "text_b": TEXT_A},
    ],
    ids=["question-hash", "model", "text_a", "text_b", "swapped-order"],
)
def test_cache_key_changes_with_each_input(changed: dict[str, Any]) -> None:
    """DESIGN §2.6 Key: {v, question_id, question_hash, model_requested, text_a, text_b}."""
    base: dict[str, Any] = {"question": REL_V1, "model": MODEL, "text_a": TEXT_A, "text_b": TEXT_B}

    assert cache_key(**{**base, **changed}) != cache_key(**base)


def test_cache_key_distinguishes_none_from_empty_text_b() -> None:
    """DESIGN §2.6 Key: `text_b` is null for stories, which is not an empty pair text."""
    assert cache_key(ATOMIC_V2, MODEL, TEXT_A, None) != cache_key(ATOMIC_V2, MODEL, TEXT_A, "")


# --- AnswerCache ----------------------------------------------------------------------------------


def test_unknown_key_returns_none(tmp_cache_dir: Path) -> None:
    """DESIGN §8 AnswerCache.get: a miss is None, on a new cache and on a populated one."""
    path = tmp_cache_dir / "answers.jsonl"
    cache = AnswerCache(path)
    try:
        assert cache.get("0" * 64) is None
        cache.put(
            cache_key(REL_V1, MODEL, TEXT_A, TEXT_B),
            REL_ANSWER,
            text_hash_a=HASH_A,
            text_hash_b=HASH_B,
        )
        assert cache.get("0" * 64) is None
    finally:
        cache.close()


def test_put_is_visible_at_once_and_persists_across_instances(tmp_cache_dir: Path) -> None:
    """DESIGN §2.6 Storage and §2.7 Checkpoint: the cache survives the process."""
    path = tmp_cache_dir / "answers.jsonl"
    pair_key = cache_key(REL_V1, MODEL, TEXT_A, TEXT_B)
    story_key = cache_key(ATOMIC_V2, MODEL, TEXT_A, None)

    first = AnswerCache(path)
    first.put(pair_key, REL_ANSWER, text_hash_a=HASH_A, text_hash_b=HASH_B)
    first.put(story_key, ATOMIC_ANSWER, text_hash_a=HASH_A, text_hash_b=None)
    assert first.get(pair_key) == REL_ANSWER
    first.close()

    second = AnswerCache(path)
    try:
        assert second.get(pair_key) == REL_ANSWER
        assert second.get(story_key) == ATOMIC_ANSWER
    finally:
        second.close()


def test_last_write_wins(tmp_cache_dir: Path) -> None:
    """DESIGN §2.6 Storage: on load the last write wins (e.g. after `--refresh`)."""
    path = tmp_cache_dir / "answers.jsonl"
    key = cache_key(REL_V1, MODEL, TEXT_A, TEXT_B)
    newer = dataclasses.replace(REL_ANSWER, choice="A_contains_B", gen_id="gen-dec-newer")

    first = AnswerCache(path)
    first.put(key, REL_ANSWER, text_hash_a=HASH_A, text_hash_b=HASH_B)
    first.put(key, newer, text_hash_a=HASH_A, text_hash_b=HASH_B)
    assert first.get(key) == newer
    first.close()

    second = AnswerCache(path)
    try:
        assert second.get(key) == newer
    finally:
        second.close()


def test_torn_last_line_is_skipped_and_later_writes_survive(tmp_cache_dir: Path) -> None:
    """DESIGN §2.6 Storage: a torn tail (crash mid-write) is skipped on load."""
    path = tmp_cache_dir / "answers.jsonl"
    kept_key = cache_key(REL_V1, MODEL, TEXT_A, TEXT_B)
    torn_key = cache_key(ATOMIC_V2, MODEL, TEXT_A, None)
    later_key = cache_key(ATOMIC_V2, MODEL, TEXT_B, None)

    first = AnswerCache(path)
    first.put(kept_key, REL_ANSWER, text_hash_a=HASH_A, text_hash_b=HASH_B)
    first.put(torn_key, ATOMIC_ANSWER, text_hash_a=HASH_A, text_hash_b=None)
    first.close()
    jsonl = _cache_file(path)
    data = jsonl.read_bytes()
    last_line = data.rstrip(b"\n").rsplit(b"\n", 1)[-1]
    jsonl.write_bytes(data.rstrip(b"\n")[: -(len(last_line) // 2)])

    second = AnswerCache(path)
    assert second.get(kept_key) == REL_ANSWER
    assert second.get(torn_key) is None
    second.put(later_key, ATOMIC_ANSWER, text_hash_a=HASH_B, text_hash_b=None)
    second.close()

    third = AnswerCache(path)
    try:
        assert third.get(kept_key) == REL_ANSWER
        assert third.get(later_key) == ATOMIC_ANSWER
    finally:
        third.close()
