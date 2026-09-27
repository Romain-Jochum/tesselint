"""The frozen Jev questions and their hashes (DESIGN §2.1 Question set, §8 questions.py)."""

import copy
import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest

from tesselint.models import Relation
from tesselint.questions import ATOMIC_V2, REL_V1, Question

REL_V1_INSTRUCTIONS = (
    "How does user story A relate to user story B, in terms of the capability each asks for?"
)
REL_V1_OPTIONS = [
    ("duplicate", "A and B ask for the same capability; either could replace the other."),
    ("A_contains_B", "Everything B asks for is part of A, and A asks for more."),
    ("B_contains_A", "Everything A asks for is part of B, and B asks for more."),
    (
        "partial_overlap",
        "A and B share part of a capability, and each also asks for something the other does not.",
    ),
    (
        "same_area_distinct",
        "A and B are in the same feature area but ask for different capabilities.",
    ),
    ("unrelated", "A and B are about different feature areas."),
]
ATOMIC_V2_INSTRUCTIONS = (
    "This user story should be split: it asks for two or more separate capabilities, each of "
    "which a team could build, ship and test on its own. Acceptance criteria, examples, rules, "
    "constraints and edge cases of a single capability do not count as separate capabilities."
)


def test_rel_v1_is_a_choice_with_the_measured_wording_in_order() -> None:
    """DESIGN §2.1 rel.v1: instructions and the six options, in this order."""
    assert REL_V1.id == "rel.v1"
    assert REL_V1.payload["type"] == "choice"
    assert REL_V1.payload["instructions"] == REL_V1_INSTRUCTIONS
    assert list(REL_V1.payload["criteria"].items()) == REL_V1_OPTIONS


def test_rel_v1_options_are_the_relation_values() -> None:
    """DESIGN §8 models.Relation holds the rel.v1 option values, in the same order."""
    assert list(REL_V1.payload["criteria"]) == [r.value for r in Relation]


def test_atomic_v2_is_a_noul_with_the_measured_wording() -> None:
    """DESIGN §2.1 atomic.v2: a noul question with the frozen instructions."""
    assert ATOMIC_V2.id == "atomic.v2"
    assert dict(ATOMIC_V2.payload) == {"type": "noul", "instructions": ATOMIC_V2_INSTRUCTIONS}


@pytest.mark.parametrize("question", [REL_V1, ATOMIC_V2], ids=["rel.v1", "atomic.v2"])
def test_hash_is_sha256_of_canonical_payload_and_stable(question: Question) -> None:
    """DESIGN §8: hash = sha256 of json.dumps(payload, separators=(",", ":")), insertion order."""
    canonical = json.dumps(question.payload, separators=(",", ":"))
    rebuilt = Question(question.id, copy.deepcopy(dict(question.payload)))

    assert question.hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert rebuilt.hash == question.hash


def test_frozen_question_hashes() -> None:
    """The measured questions key the answer cache: any change to them is a new version."""
    assert REL_V1.hash == "b469c4129a85f430a342af3b30092c7be888080073059e26e69dca2f79ce6834"
    assert ATOMIC_V2.hash == "deb719a44988ae08e195a6fa3deefba77dddc240ede90a8433d80eaf18c59751"


def _set_instructions(p: dict[str, Any]) -> None:
    p["instructions"] = p["instructions"].rstrip("?") + "."


def _reword_option(p: dict[str, Any]) -> None:
    p["criteria"]["unrelated"] = "A and B are about unrelated feature areas."


def _drop_option(p: dict[str, Any]) -> None:
    del p["criteria"]["duplicate"]


def _add_option(p: dict[str, Any]) -> None:
    p["criteria"]["related"] = "A and B are related."


def _change_type(p: dict[str, Any]) -> None:
    p["type"] = "score"


def _reorder_options(p: dict[str, Any]) -> None:
    p["criteria"] = dict(reversed(list(p["criteria"].items())))


@pytest.mark.parametrize(
    "mutate",
    [_set_instructions, _reword_option, _drop_option, _add_option, _change_type, _reorder_options],
    ids=[
        "instructions",
        "option-wording",
        "option-removed",
        "option-added",
        "type",
        "option-order",
    ],
)
def test_hash_changes_when_any_payload_field_changes(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    """DESIGN §2.1: a rewording is a new version and a new cache key."""
    payload = copy.deepcopy(dict(REL_V1.payload))
    mutate(payload)

    assert Question(REL_V1.id, payload).hash != REL_V1.hash
