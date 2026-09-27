"""The frozen Jev questions (DESIGN §2.1). A rewording is a new version and a new cache key.

Each payload is one entry of the request's `questions` map, in the format the probes used:
`POST {base}/alpha/decisions` with `{"model": ..., "state": ..., "questions": {key: payload}}`.
The probes sent `rel.v1` under the key `rel` and `atomic.v2` under `atomic` (the id before the
dot); the answer comes back under the same key in `answers` (see tests/fixtures/jev_bodies.json).
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Question:
    """A versioned Jev question; `payload` is sent verbatim."""

    id: str
    payload: Mapping[str, Any] = field(hash=False)

    @property
    def hash(self) -> str:
        """Return the sha256 of the compact JSON payload in insertion order.

        Keys are not sorted: the option order is what Jev sees, so reordering is a new version.
        """
        canonical = json.dumps(self.payload, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


REL_V1 = Question(
    id="rel.v1",
    payload={
        "type": "choice",
        "instructions": (
            "How does user story A relate to user story B, "
            "in terms of the capability each asks for?"
        ),
        "criteria": {
            "duplicate": "A and B ask for the same capability; either could replace the other.",
            "A_contains_B": "Everything B asks for is part of A, and A asks for more.",
            "B_contains_A": "Everything A asks for is part of B, and B asks for more.",
            "partial_overlap": (
                "A and B share part of a capability, "
                "and each also asks for something the other does not."
            ),
            "same_area_distinct": (
                "A and B are in the same feature area but ask for different capabilities."
            ),
            "unrelated": "A and B are about different feature areas.",
        },
    },
)

ATOMIC_V2 = Question(
    id="atomic.v2",
    payload={
        "type": "noul",
        "instructions": (
            "This user story should be split: it asks for two or more separate capabilities, "
            "each of which a team could build, ship and test on its own. Acceptance criteria, "
            "examples, rules, constraints and edge cases of a single capability do not count "
            "as separate capabilities."
        ),
    },
)
