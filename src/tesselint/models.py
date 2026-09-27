"""Shared types (DESIGN §8, models.py). Pure data: no behaviour lives here."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal


class Relation(StrEnum):
    """The six `rel.v1` options, values as sent to Jev (DESIGN §2.1)."""

    DUPLICATE = "duplicate"
    A_CONTAINS_B = "A_contains_B"
    B_CONTAINS_A = "B_contains_A"
    PARTIAL = "partial_overlap"
    SAME_AREA = "same_area_distinct"
    UNRELATED = "unrelated"


class ProposalKind(StrEnum):
    """Kinds of proposal written to proposals.md (DESIGN §2.5, §9)."""

    MERGE = "merge"
    OVERLAP = "overlap"
    SPLIT = "split"
    LEAVE = "leave"


Order = Literal["fwd", "swap"]


@dataclass(frozen=True, slots=True)
class Story:
    """One live story parsed from §4 of the source."""

    id: str
    epic: str
    title: str
    tag: str
    core: str
    subs: tuple[str, ...]
    line: int


@dataclass(frozen=True, slots=True)
class Tombstone:
    """A removed story kept as a pointer; `target` is where it went, if anywhere."""

    id: str
    epic: str
    title: str
    target: str | None
    line: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The exact §4 bytes a run works from (DESIGN §9, snapshot.md)."""

    path: Path
    text: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Output of parse_section; `unparsed` holds (line number, line) of stray `US-` lines."""

    stories: tuple[Story, ...]
    tombstones: tuple[Tombstone, ...]
    unparsed: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Decision thresholds (DESIGN §2.5, §5); lives here so config and proposals share it."""

    swap_gate: float = 0.2
    merge: float = 0.5
    cluster: float = 0.5
    split: float = 0.8
    split_corroborated: float = 0.7
    related: float = 0.4
    min_words_atomic: int = 8
    borderline: float = 0.1
