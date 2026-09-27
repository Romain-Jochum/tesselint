"""Parser, render and v1 import on the public synthetic backlog (`fixtures/sample_backlog.md`).

The file is fictional and small (21 live stories, 2 tombstones in 4 epics) but carries every
format variant of a real §4: heading styles, qualified tags, an empty core, nested sub-bullets, a
table, `US-x.y`/`§x`/`(G1)`/`(P2)` references, both tombstone forms and a story filed under an
epic other than its ID's. Public CI keeps parser coverage through it.
"""

from functools import cache
from pathlib import Path

import pytest

from tesselint.cli import main
from tesselint.inputv1 import export_markdown, import_markdown, to_parse_result, validate_v1
from tesselint.models import ParseResult, Story
from tesselint.parse import epic_names, parse_section, select_epics, take_snapshot
from tesselint.render import render, text_hash
from tests.conftest import SAMPLE_BACKLOG

SAMPLE = SAMPLE_BACKLOG


@cache
def _parsed() -> ParseResult:
    return parse_section(SAMPLE.read_text("utf-8"))


def _story(story_id: str) -> Story:
    matches = [s for s in _parsed().stories if s.id == story_id]
    assert len(matches) == 1, f"{story_id} parsed {len(matches)} times"
    return matches[0]


def test_counts_per_epic_and_nothing_unparsed() -> None:
    parsed = _parsed()
    per_epic = {e: [s.id for s in parsed.stories if s.epic == e] for e in ("E1", "E2", "E3", "E4")}

    assert (len(parsed.stories), len(parsed.tombstones), parsed.unparsed) == (21, 2, ())
    assert {e: len(ids) for e, ids in per_epic.items()} == {"E1": 5, "E2": 6, "E3": 5, "E4": 5}
    assert per_epic["E3"] == ["US-3.1", "US-3.2", "US-3.3", "US-1.5", "US-3.5"]


def test_both_tombstone_forms() -> None:
    """`— merged into US-x: reason` keeps its target; `— removed: reason` has none."""
    tombs = [(t.id, t.epic, t.title, t.target) for t in _parsed().tombstones]

    assert tombs == [
        ("US-1.7", "E1", "Single sign-on [MVP]", "US-1.1"),
        ("US-4.6", "E4", "Automatic fines [MVP]", None),
    ]
    assert {"US-1.7", "US-4.6"}.isdisjoint(s.id for s in _parsed().stories)


@pytest.mark.parametrize(
    ("story_id", "title", "tag"),
    [
        ("US-1.2", "Proof of address", "MVP; pilot neighbourhood only"),
        ("US-2.3", "Availability calendar", "MVP+"),
        ("US-2.5", "Safety kit suggestions", "Lite: volunteers curate the list"),
        ("US-1.6", "Pause my membership", "Not now"),
        ("US-3.5", "Group loans", "decision"),
    ],
)
def test_title_and_qualified_tag_split(story_id: str, title: str, tag: str) -> None:
    story = _story(story_id)
    assert (story.title, story.tag) == (title, tag)


def test_heading_variants_and_foreign_epic(tmp_path: Path) -> None:
    """`### E1 Name`, `### E2: Name`, `### E3 — Name`; US-1.5 sits under E3 but selects with E1."""
    snapshot = take_snapshot(SAMPLE, tmp_path / "run")

    assert epic_names(snapshot) == [
        ("E1", "Membership"),
        ("E2", "Catalogue"),
        ("E3", "Borrowing"),
        ("E4", "Returns and repairs"),
    ]
    assert _story("US-1.5").epic == "E3"
    stories = _parsed().stories
    assert "US-1.5" in [s.id for s in select_epics(stories, ["E1"])]
    assert "US-1.5" in [s.id for s in select_epics(stories, ["E3"])]
    assert "US-1.5" not in [s.id for s in select_epics(stories, ["E2"])]


def test_nested_bullets_and_table_rows_become_subs() -> None:
    assert _story("US-3.1").subs == (
        "A reservation lasts one loan period (US-2.6).",
        "I can hold at most **two** reservations at a time.",
        "Volunteers can lift the limit for community projects.",
        "The limit counts power tools twice.",
    )
    # The header row stays, the separator row goes; cells join with "; ".
    assert _story("US-2.6").subs == (
        "Kind; Deposit; Loan length",
        "Hand tool; €5; 7 days",
        "Power tool; €20; 3 days",
    )


def test_empty_core_renders_from_the_first_sub() -> None:
    story = _story("US-2.4")

    assert story.core == ""
    assert render(story) == (
        "Tool page. Every tool has a photo, its condition, the deposit and a short safety note.\n"
        "- Power tools also list the protective gear to borrow with them ."
    )
    assert render(_story("US-4.5")) == "Seasonal stock-take."


@pytest.mark.parametrize(
    ("story_id", "core_text"),
    [
        # (US-1.1, 3.1) short-form reference group
        (
            "US-3.3",
            "Pickup reminder. I get a text message the morning of my pickup day, so that I don't"
            " forget to come .",
        ),
        # (G1) and a qualified tag
        (
            "US-2.5",
            "Safety kit suggestions. When I reserve a power tool, I'm offered the matching gloves,"
            " goggles or ear defenders, so that I don't start a job unprotected .",
        ),
        # (P2) persona and markup
        (
            "US-1.2",
            "Proof of address. I upload a photo of a utility bill, so that a volunteer can confirm"
            " I live nearby.",
        ),
        # backticks go, a plain parenthesis stays
        (
            "US-2.2",
            "Search. I search the catalogue by name or by job (cut a hedge), so that I find the"
            " right tool fast .",
        ),
    ],
)
def test_core_render_strips_markup_and_references(story_id: str, core_text: str) -> None:
    assert render(_story(story_id), "core") == core_text


def test_full_render_strips_section_refs_bold_and_italics() -> None:
    assert render(_story("US-2.3")).splitlines()[1:] == [
        "- Reserved days are greyed out.",
        "- Days the shed is closed are hidden.",
    ]
    assert "- I can hold at most two reservations at a time." in render(_story("US-3.1"))
    assert render(_story("US-4.2")).endswith(
        "- The tool is marked in repair until a volunteer checks it ."
    )


def test_v1_import_keeps_every_text_hash() -> None:
    """INPUT.md §3: the v1 import renders to the same texts, IDs, order and tombstones."""
    legacy = _parsed()
    v1 = to_parse_result(import_markdown(SAMPLE))

    assert [s.id for s in v1.stories] == [s.id for s in legacy.stories]
    assert [text_hash(render(s)) for s in v1.stories] == [
        text_hash(render(s)) for s in legacy.stories
    ]
    assert [render(s, "core") for s in v1.stories] == [render(s, "core") for s in legacy.stories]
    assert [(t.id, t.target) for t in v1.tombstones] == [("US-1.7", "US-1.1"), ("US-4.6", None)]


def test_v1_import_fields_and_its_single_warning() -> None:
    doc = import_markdown(SAMPLE)
    rows = {row["id"]: row for row in doc["stories"]}

    problems = validate_v1(doc)

    assert [(p.story_id, p.severity) for p in problems] == [("US-4.5", "warning")]
    assert [row["id"] for row in doc["stories"]][:6] == [
        "US-1.1",
        "US-1.2",
        "US-1.3",
        "US-1.4",
        "US-1.6",
        "US-1.7",
    ]
    assert rows["US-1.2"]["priority"] == "MVP"
    assert rows["US-2.3"]["priority"] == "MVP+"
    assert rows["US-1.6"]["priority"] == "Not now"
    assert rows["US-3.3"]["references"] == ["US-3.1", "US-1.1"]
    assert (rows["US-1.7"]["status"], rows["US-1.7"]["merged_into"]) == ("removed", "US-1.1")
    assert rows["US-1.7"]["tag"] == "MVP"
    assert rows["US-4.6"]["merged_into"] is None
    assert rows["US-4.6"]["removed_reason"] == (
        "fines put off the people the shed most wants to reach."
    )


def test_export_then_parse_round_trips(tmp_path: Path) -> None:
    """Export writes both tombstone forms (`— merged into` and `— removed`) and parses back."""
    doc = import_markdown(SAMPLE)
    exported = tmp_path / "exported.md"
    exported.write_text(export_markdown(doc), encoding="utf-8")

    reparsed = parse_section(exported.read_text("utf-8"))

    assert reparsed.unparsed == ()
    key = [(s.id, s.epic, s.title, s.tag, s.core, s.subs) for s in _parsed().stories]
    assert [(s.id, s.epic, s.title, s.tag, s.core, s.subs) for s in reparsed.stories] == key
    assert [(t.id, t.target) for t in reparsed.tombstones] == [
        ("US-1.7", "US-1.1"),
        ("US-4.6", None),
    ]


def test_cli_parse_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    try:
        code = main(["parse", "--source", str(SAMPLE)])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    out = capsys.readouterr().out

    assert code == 0
    assert "21 stories, 2 tombstones" in out
