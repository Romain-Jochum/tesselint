"""Snapshot, §4 parsing and epic selection (DESIGN §8 parse.py, §9 snapshot)."""

import hashlib
from functools import cache
from pathlib import Path

import pytest

from tesselint.models import ParseResult, Story
from tesselint.parse import load_snapshot, parse_section, select_epics, take_snapshot
from tests.conftest import require_section4

SECTION4_SHA256 = "b2dd2ef8cf7bd67fe151a1ca270f310efe3691fc58a41b1d7f1208a90611b14c"


def digest(text: str) -> str:
    """The private fixture's wording stays private: its texts are pinned by a short sha256."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@cache
def _parsed() -> ParseResult:
    return parse_section(require_section4().read_text("utf-8"))


def _story(story_id: str) -> Story:
    matches = [s for s in _parsed().stories if s.id == story_id]
    assert len(matches) == 1, f"{story_id} parsed {len(matches)} times"
    return matches[0]


# --- parse_section on the real §4 fixture ---------------------------------------------------------


def test_real_section_has_176_stories_and_no_unparsed_lines() -> None:
    """DESIGN §8 parse_section; PROBLEM: 179 `- **US-` lines = 176 live + 3 tombstones."""
    result = _parsed()

    assert len(result.stories) == 176
    assert len(result.tombstones) == 3
    assert result.unparsed == ()


def test_story_ids_are_unique() -> None:
    """DESIGN §8: a story id identifies one story (matrix rows, cache, proposals)."""
    ids = [s.id for s in _parsed().stories]

    assert len(set(ids)) == len(ids)


def test_tombstones_keep_their_merge_targets() -> None:
    """DESIGN §8 Tombstone; PROBLEM: `- **US-x.y Title** — merged into US-a.b[: reason].`"""
    assert [(t.id, t.epic, digest(t.title), t.target, t.line) for t in _parsed().tombstones] == [
        ("US-20.3", "E20", "1a79403bbd8a5acb", "US-1.19", 222),
        ("US-3.5", "E3", "2f816afa91ab5f9b", "US-3.3", 304),
        ("US-3.6", "E3", "52742e5a89e231f4", "US-3.3", 305),
    ]


@pytest.mark.parametrize(
    ("story_id", "epic", "line"),
    [
        ("US-1.10", "E1", 21),
        ("US-1.1", "E2", 195),
        ("US-20.2", "E20", 217),
        ("US-8.6", "E8", 473),
        ("US-18.4", "E13", 674),
    ],
    ids=["first-story", "US-1.1-under-E2", "E20", "empty-core", "US-18.4-under-E13"],
)
def test_story_epic_is_its_heading_and_line_is_one_based(
    story_id: str, epic: str, line: int
) -> None:
    """DESIGN §8: Story.epic is the `### E<n>` heading; Story.line is 1-based in snapshot.md."""
    story = _story(story_id)

    assert (story.epic, story.line) == (epic, line)


@pytest.mark.parametrize(
    ("story_id", "tag"),
    [
        ("US-1.1", "MVP"),
        ("US-3.3", "0870649484fe2d1e"),  # `MVP; <qualifier>`
        ("US-17.2", "75d75802653c0430"),  # `MVP, <qualifier>`
        ("US-23.18", "d12f0c7f08da22ff"),  # `decision, before US-23.8`
        ("US-23.8", "52026fcde07cebfb"),  # `Later: <qualifier>`
        ("US-23.9", "Not now"),
    ],
)
def test_tags_are_kept_raw(story_id: str, tag: str) -> None:
    """DESIGN §9 proposals.md shows the raw tag, e.g. `tag [MVP; note]` (qualified ones hashed)."""
    got = _story(story_id).tag
    assert (got if tag in {"MVP", "Not now"} else digest(got)) == tag


def test_title_tag_and_core_are_split_at_the_bold_marker() -> None:
    """DESIGN §8 Story: `- **US-x.y Title [tag].** core`."""
    story = _story("US-20.2")

    assert digest(story.title) == "c712840ed739b343"
    assert story.tag == "MVP"
    assert digest(story.core) == "7c71df0dc8a727eb"
    assert not story.core.startswith(("**", "[", "."))


@pytest.mark.parametrize("story_id", ["US-8.6", "US-23.9", "US-18.5"])
def test_empty_core_stories_have_an_empty_core(story_id: str) -> None:
    """PROBLEM variants: US-8.6 and US-23.9 hold text only in sub-bullets, US-18.5 has none."""
    assert _story(story_id).core == ""


@pytest.mark.parametrize(
    ("story_id", "n_subs"),
    [("US-20.1", 4), ("US-20.2", 4), ("US-1.1", 13), ("US-8.6", 2), ("US-23.9", 5), ("US-18.5", 0)],
    ids=["US-20.1", "US-20.2", "US-1.1-flattens-4-nested", "US-8.6", "US-23.9", "US-18.5-none"],
)
def test_sub_bullet_counts(story_id: str, n_subs: int) -> None:
    """DESIGN §2.1: one entry per sub-bullet, nested (4-space) bullets flattened."""
    assert len(_story(story_id).subs) == n_subs


@pytest.mark.parametrize(
    ("story_id", "index", "sub_digest"),
    [
        ("US-1.1", 6, "dd16551663359280"),
        ("US-1.2", 14, "b988f27fcd47fedf"),
        ("US-3.3", 2, "eb14b002f2ac676e"),
    ],
    ids=["4-space-nested-bullet", "after-blank-line-and-undashed-paragraph", "indented-table"],
)
def test_sub_bullet_variants_stay_with_their_story(
    story_id: str, index: int, sub_digest: str
) -> None:
    """PROBLEM variants: nested bullets, blank lines inside a story, the US-3.3 table."""
    assert digest(_story(story_id).subs[index]) == sub_digest


@pytest.mark.parametrize(
    ("story_id", "last_sub"),
    [
        ("US-8.6", "e251759511d92659"),
        ("US-20.1", "b38901f257e95ae9"),
        ("US-20.2", "57a5acc9b399654d"),
        ("US-23.9", "16223014b0561d9d"),
        ("US-18.3", "60376495ae7e26ec"),
    ],
    ids=["next-story", "next-in-epic", "before-tombstone", "empty-core", "before-empty-story"],
)
def test_story_never_absorbs_what_follows_it(story_id: str, last_sub: str) -> None:
    """DESIGN §8: a story's subs end at the next story, tombstone or heading."""
    assert digest(_story(story_id).subs[-1]) == last_sub


def test_unparseable_story_line_lands_in_unparsed() -> None:
    """DESIGN §8 ParseResult.unparsed: (1-based line, line) of `- **US-` lines that don't parse."""
    text = (
        "## 4. User stories\n"
        "\n"
        "### E1 Onboarding\n"
        "\n"
        "- **US-1.1 Good story [MVP].** I do a thing, so that it works.\n"
        "- **US-1.2 Broken story without a closing marker [MVP]. I do another thing.\n"
        "- **US-1.3 Another good story [Lite].** I do a third thing.\n"
    )

    result = parse_section(text)

    assert [s.id for s in result.stories] == ["US-1.1", "US-1.3"]
    assert len(result.unparsed) == 1
    line_no, line = result.unparsed[0]
    assert line_no == 6
    assert "US-1.2 Broken story" in line


# --- take_snapshot / load_snapshot ----------------------------------------------------------------


def _source_with_neighbours(tmp_path: Path) -> Path:
    source = tmp_path / "backlog.md"
    before = "# User stories\n\n## 3. Product\n\nSome prose mentioning US-1.1.\n\n"
    after = "## 5. Workflows\n\n- **US-99.1 Not a story [MVP].** Must not be copied.\n"
    section = require_section4().read_bytes()
    source.write_bytes(before.encode("utf-8") + section + after.encode("utf-8"))
    return source


def test_take_snapshot_copies_section4_bytes_and_writes_its_hash(tmp_path: Path) -> None:
    """DESIGN §9: snapshot.md = exact §4 bytes up to `## 5.`; hash file `<hex>  snapshot.md`."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    snapshot = take_snapshot(_source_with_neighbours(tmp_path), run_dir)

    written = (run_dir / "snapshot.md").read_bytes()
    assert written == require_section4().read_bytes()
    assert hashlib.sha256(written).hexdigest() == SECTION4_SHA256
    assert (run_dir / "snapshot.sha256").read_text("utf-8").rstrip("\n") == (
        f"{SECTION4_SHA256}  snapshot.md"
    )
    assert snapshot.path == run_dir / "snapshot.md"
    assert snapshot.sha256 == SECTION4_SHA256
    assert snapshot.text == require_section4().read_text("utf-8")


def test_load_snapshot_round_trips(tmp_path: Path) -> None:
    """DESIGN §2.7 `--resume` reuses the run's snapshot; §9 snapshot files."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    taken = take_snapshot(_source_with_neighbours(tmp_path), run_dir)

    assert load_snapshot(run_dir) == taken


# --- select_epics ---------------------------------------------------------------------------------


def _planted(story_id: str, epic: str, line: int) -> Story:
    return Story(id=story_id, epic=epic, title="t", tag="MVP", core="c", subs=(), line=line)


PLANTED = (
    _planted("US-1.10", "E1", 10),
    _planted("US-1.1", "E2", 20),
    _planted("US-2.1", "E2", 30),
    _planted("US-20.1", "E20", 40),
    _planted("US-20.2", "E20", 50),
    _planted("US-3.3", "E3", 60),
    _planted("US-18.4", "E13", 70),
)


@pytest.mark.parametrize(
    ("epics", "expected"),
    [
        (("E1", "E2", "E20"), ["US-1.10", "US-1.1", "US-2.1", "US-20.1", "US-20.2"]),
        (("E20", "E1", "E2"), ["US-1.10", "US-1.1", "US-2.1", "US-20.1", "US-20.2"]),
        (("E1",), ["US-1.10", "US-1.1"]),
        (("E2",), ["US-1.1", "US-2.1"]),
        (("E13",), ["US-18.4"]),
        (("E18",), ["US-18.4"]),
        (None, [s.id for s in PLANTED]),
    ],
    ids=[
        "t1",
        "t1-any-order",
        "id-epic",
        "heading-epic",
        "E13-heading",
        "E18-id",
        "none-keeps-all",
    ],
)
def test_select_epics_keeps_heading_or_id_epic_in_document_order(
    epics: tuple[str, ...] | None, expected: list[str]
) -> None:
    """DESIGN §8 select_epics: heading epic OR ID epic (US-<n>.x -> E<n>); None keeps all."""
    selected = select_epics(PLANTED, None if epics is None else set(epics))

    assert [s.id for s in selected] == expected


def test_select_epics_tier1_on_real_section() -> None:
    """DESIGN §8 TIERS t1 = E1, E2, E20: 19 + 11 (with US-1.1) + 5 live stories (PROBLEM)."""
    selected = select_epics(_parsed().stories, {"E1", "E2", "E20"})

    assert len(selected) == 35
    assert {"US-1.1", "US-20.1", "US-20.2"} <= {s.id for s in selected}
    assert [s.line for s in selected] == sorted(s.line for s in selected)


def test_removed_tombstones_are_not_live_stories() -> None:
    """A `— removed: reason` line is a tombstone with no target (INPUT.md §5)."""
    text = (
        "## 4. User stories\n\n### E1 Accounts\n\n"
        "- **US-1.1 Sign in [MVP].** I sign in with a link, so that I skip passwords.\n"
        "- **US-1.2 Remember me** — removed: not relevant (coordinator, 2026-09-26).\n"
        "- **US-1.3 Weekly digest (G15)** — removed: the dashboard covers it (US-15.5).\n"
        "- **US-1.4 Old sign in** — merged into US-1.1: same capability.\n\n## 5. Workflows\n"
    )
    result = parse_section(text)

    assert [s.id for s in result.stories] == ["US-1.1"]
    assert [(t.id, t.target) for t in result.tombstones] == [
        ("US-1.2", None),
        ("US-1.3", None),
        ("US-1.4", "US-1.1"),
    ]
    assert result.unparsed == ()
