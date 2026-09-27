"""Story text as sent to Jev: clean, render, text_hash (DESIGN §2.1 Rendering)."""

import hashlib

import pytest

from tesselint.models import Story
from tesselint.parse import parse_section
from tesselint.render import clean, render, text_hash
from tests.conftest import require_section4


def _planted(title: str, core: str, subs: tuple[str, ...] = ()) -> Story:
    return Story(id="US-9.9", epic="E9", title=title, tag="MVP", core=core, subs=subs, line=1)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _real(story_id: str) -> Story:
    stories = parse_section(require_section4().read_text("utf-8")).stories
    return next(s for s in stories if s.id == story_id)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a **bold** word", "a bold word"),
        ("run `tesselint parse` first", "run tesselint parse first"),
        ("_as a borrower_, I want", "as a borrower, I want"),
        ("Company page [MVP; pilot only] now", "Company page now"),
        ("see US-20.2 for the check", "see for the check"),
        ("profile changes (US-1.1) and posts", "profile changes and posts"),
        ("Drills (US-1.19, 7.1) and saws", "Drills and saws"),
        ("per §3b the engine", "per the engine"),
        ("Loan reminders (G10) today", "Loan reminders today"),
        ("the volunteer (P2) view", "the volunteer view"),
        ("  two   spaces\tand  tabs  ", "two spaces and tabs"),
        ("Café, résumé et Société Générale", "Café, résumé et Société Générale"),
    ],
    ids=[
        "bold",
        "backticks",
        "underscores",
        "bracketed-tag",
        "bare-story-ref",
        "parenthesised-ref",
        "short-form-refs",
        "section-ref",
        "G-code",
        "P-code",
        "whitespace",
        "non-ascii-kept",
    ],
)
def test_clean_strip_rules(raw: str, expected: str) -> None:
    """DESIGN §2.1 clean: strips `**`, backticks, `_`, `[...]`, story refs, §x, (G<n>), (P<n>)."""
    assert clean(raw) == expected


@pytest.mark.parametrize(
    ("story_id", "forbidden"),
    [
        ("US-1.1", "US-20.1"),
        ("US-1.1", "**"),
        ("US-1.1", "["),
        ("US-20.2", "§"),
        ("US-23.9", "(G10)"),
        ("US-3.3", "|"),
        ("US-3.3", ":--"),
    ],
)
def test_rendered_real_story_has_no_markup_or_refs(story_id: str, forbidden: str) -> None:
    """DESIGN §2.1: full text is cleaned, sub-bullets included; table rows are flattened."""
    assert forbidden not in render(_real(story_id))


def test_rendered_real_story_starts_with_title_then_core() -> None:
    """DESIGN §2.1: text = clean(title + ". " + core), then the sub-bullet lines."""
    text = render(_real("US-1.1"))

    lines = text.split("\n")
    # The private fixture's wording stays private: its lines are pinned by a short sha256.
    assert [_digest(lines[0]), _digest(lines[1])] == ["01c6804d3fab0c4c", "fa6901a3bd895db5"]
    assert lines[1].startswith("- ")


def test_render_full_adds_one_line_per_sub_and_core_does_not() -> None:
    """DESIGN §2.1 and §7 `--render full|core`: full = core text plus one `- ` line per sub."""
    story = _planted(
        "Weekly records",
        "I see **my** records so that I improve.",
        ("Busiest day for loans", "A `fastest` reply"),
    )

    assert render(story, "core") == "Weekly records. I see my records so that I improve."
    assert render(story, "full") == (
        "Weekly records. I see my records so that I improve.\n"
        "- Busiest day for loans\n"
        "- A fastest reply"
    )
    assert render(story) == render(story, "full")


@pytest.mark.parametrize(
    ("story", "expected_first_line"),
    [
        (
            _planted(
                "Rate limits",
                "",
                (
                    "Two-hour slots for every power tool.",
                    "A paused platform says so.",
                ),
            ),
            "Rate limits. Two-hour slots for every power tool.",
        ),
        (_planted("Sign-up and payment", ""), "Sign-up and payment."),
    ],
    ids=["takes-first-sub", "no-subs"],
)
def test_empty_core_takes_the_first_sub_bullet(story: Story, expected_first_line: str) -> None:
    """DESIGN §2.1: an empty core takes the first sub-bullet (US-8.6, US-23.9; US-18.5 has none)."""
    assert render(story, "core") == expected_first_line
    assert render(story, "full").split("\n")[0] == expected_first_line


@pytest.mark.parametrize(
    "text",
    ["", "Tool catalogue. The shed lists every tool.", "Société Générale — résumé"],
)
def test_text_hash_is_hex_sha256_of_utf8(text: str) -> None:
    """DESIGN §2.1 forward order and §2.6 cache: text_hash is the stable sha256 hex digest."""
    assert text_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()
