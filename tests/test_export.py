"""Export of accepted decisions as JSON and Markdown (docs/ui/DESIGN.md §2 Export, §7.4).

Planted decision views and run bundle for ordering, filtering, warnings and Do lines; a planted
schema-valid document for the Markdown; the UI fixture for schema validity end to end.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from tesselint.derive import derive
from tesselint.models import Story, Thresholds
from tesselint.render import render, text_hash
from tesselint.server import DecisionStore, RunStore, build_export, export_markdown_doc
from tesselint.server.export import DO_LINES, EXPORT_FORMAT, EXPORT_INTRO
from tesselint.server.payloads import DecisionView, Kind, RunBundle, StoryRow
from tests.test_server_api import load_ui_fixture, schema_errors

REF = "t3~20260926-120000"
SNAP = "c" * 64
STORIES = [
    Story("US-1.1", "E1", "Profile page", "", "As a member I can view my profile.", (), 1),
    Story("US-1.2", "E1", "Profile edits", "", "As a member I can edit my profile.", (), 2),
    Story("US-1.3", "E1", "Account settings", "", "As a member I can change settings.", (), 3),
    Story("US-1.4", "E1", "Profile photo", "", "As a member I can upload a photo.", (), 4),
    Story("US-2.1", "E2", "Search", "", "As a buyer I can search listings.", (), 5),
    Story("US-2.2", "E2", "Saved searches", "", "As a buyer I can save a search.", (), 6),
    Story("US-2.3", "E2", "Search alerts", "", "As a buyer I get alerts for a search.", (), 7),
    Story("US-3.1", "E3", "Checkout", "", "As a buyer I pay and get an invoice.", ("Refunds",), 8),
]
TEXT = {s.id: render(s, "full") for s in STORIES}
TITLE = {s.id: s.title for s in STORIES}
HASH = {sid: text_hash(text) for sid, text in TEXT.items()}


def bundle() -> RunBundle:
    rows = [
        StoryRow(
            id=s.id,
            epic=s.epic,
            title=s.title,
            tag="",
            words=len(TEXT[s.id].split()),
            text_hash=HASH[s.id],
            text=TEXT[s.id],
            atomic=None,
            order=i,
        )
        for i, s in enumerate(STORIES)
    ]
    return {
        "run": {
            "ref": REF,
            "kind": "real",
            "kind_inferred": False,
            "dry_run": False,
            "status": "complete",
            "source": {"path": "stories.v1.json", "format": "json"},
            "tier": "t3",
            "n_stories": len(rows),
            "finished_at": "2026-09-26T12:05:00+00:00",
            "cost_live_usd": 0.01,
            "cost_cached_usd": 0.0,
            "snapshot_sha256": SNAP,
            "proposals": {"merge": 5, "overlap": 1, "split": 2},
        },
        "thresholds": {
            "swap_gate": 0.2,
            "merge": 0.5,
            "cluster": 0.7,
            "split": 0.8,
            "split_corroborated": 0.7,
            "related": 0.4,
            "min_words_atomic": 8,
            "borderline": 0.1,
        },
        "epics": [{"id": "E1", "name": "Profile"}, {"id": "E2", "name": "Search"}],
        "stories": rows,
        "tombstones": [],
        "clusters": [],
        "previous": None,
        "calibration": None,
        "calibration_from": None,
        "source_status": {
            "source_id": 0,
            "same_snapshot": True,
            "edited": [],
            "added": [],
            "removed": [],
            "problems": [],
        },
    }


def view(
    kind: Kind,
    stories: list[str],
    action: str | None = "accept",
    *,
    status: str = "current",
    proposed: bool = True,
    note: str = "",
) -> DecisionView:
    """A decision view on REF; a stale one has an older hash for its first story."""
    if kind == "merge":
        key, target = f"merge:{stories[0]}>{stories[1]}", stories[0]
    elif kind == "split":
        key, target = f"split:{stories[0]}", None
    else:
        key, target = "overlap:" + "+".join(sorted(stories)), "C1"
    hashes = {sid: HASH[sid] for sid in stories}
    if status == "stale":
        hashes[stories[0]] = text_hash(TEXT[stories[0]] + " (older wording)")
    return cast(
        DecisionView,
        {
            "key": key,
            "kind": kind,
            "target": target,
            "stories": stories,
            "action": action,
            "note": note,
            "text_hashes": hashes,
            "titles": {sid: TITLE[sid] for sid in stories},
            "run": REF,
            "decided_at": "2026-09-26T12:10:00+00:00",
            "status": status,
            "changed": [stories[0]] if status == "stale" else [],
            "proposed": proposed,
            "superseded_by": None,
            "applied": False if kind == "merge" else None,
        },
    )


def export(*views: DecisionView, include_stale: bool = False) -> dict[str, Any]:
    return build_export(list(views), bundle(), include_stale=include_stale)


def items_by_key(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in doc["items"]}


# --- build_export ---------------------------------------------------------------------------------


def test_items_are_merges_by_kept_story_then_splits_then_untangles() -> None:
    """§2 Export: accepted merges grouped by kept story (document order), splits, overlaps."""
    doc = export(
        view("overlap", ["US-2.2", "US-1.3"]),
        view("merge", ["US-2.1", "US-2.3"]),
        view("split", ["US-3.1"]),
        view("merge", ["US-1.1", "US-1.2"]),
        view("merge", ["US-2.1", "US-2.2"]),
        view("merge", ["US-1.1", "US-1.4"]),
    )

    assert (doc["format"], doc["version"]) == (EXPORT_FORMAT, 1)
    assert doc["run"] == {"ref": REF, "snapshot_sha256": SNAP}
    items = doc["items"]
    assert [item["order"] for item in items] == [1, 2, 3, 4, 5, 6]
    assert [item["action"] for item in items] == ["merge"] * 4 + ["split", "untangle"]
    kept = [item["key"].removeprefix("merge:").split(">")[0] for item in items[:4]]
    assert kept == ["US-1.1", "US-1.1", "US-2.1", "US-2.1"]
    assert items[4]["key"] == "split:US-3.1"
    assert items[5]["key"] == "overlap:US-1.3+US-2.2"


@pytest.mark.parametrize(
    ("decision", "exported"),
    [
        pytest.param(view("merge", ["US-1.1", "US-1.2"]), True, id="accepted"),
        pytest.param(
            view("merge", ["US-1.1", "US-1.2"], None, note="later"), False, id="note-only"
        ),
        pytest.param(view("merge", ["US-1.1", "US-1.2"], "reject"), False, id="rejected"),
        pytest.param(view("split", ["US-3.1"], "defer"), False, id="deferred"),
        pytest.param(view("split", ["US-3.1"], proposed=False), True, id="orphaned-accepted"),
    ],
)
def test_only_accepted_decisions_are_exported(decision: DecisionView, exported: bool) -> None:
    """§2 Export, §3: accepted items only; an orphaned accepted decision is still exported."""
    doc = export(decision)

    assert [item["key"] for item in doc["items"]] == ([decision["key"]] if exported else [])


@pytest.mark.parametrize("include_stale", [False, True])
def test_stale_items_are_exported_only_on_request(include_stale: bool) -> None:
    """§2 Export: stale items only with `include_stale`, marked stale."""
    current = view("merge", ["US-1.1", "US-1.2"])
    stale = view("split", ["US-3.1"], status="stale")

    items = items_by_key(export(current, stale, include_stale=include_stale))

    assert current["key"] in items
    assert (stale["key"] in items) is include_stale
    if include_stale:
        assert items[stale["key"]]["status"] == "stale"


@pytest.mark.parametrize("split_story", ["US-1.1", "US-1.2"], ids=["split-keep", "split-drop"])
def test_merge_and_split_on_one_story_warn(split_story: str) -> None:
    """§2 Export: a merge plus a split on the same story warns; unrelated items do not."""
    merge = view("merge", ["US-1.1", "US-1.2"])
    split = view("split", [split_story])
    other = view("overlap", ["US-2.1", "US-2.2"])

    items = items_by_key(export(merge, split, other))

    warnings = items[merge["key"]]["warnings"] + items[split["key"]]["warnings"]
    assert any(split_story in warning for warning in warnings), warnings
    assert items[other["key"]]["warnings"] == []


def test_merge_chain_warns_with_the_implied_merge() -> None:
    """§2 Export: X→Y and Y→Z (US-2.3 into US-2.2, US-2.2 into US-2.1) suggests X→Z."""
    first = view("merge", ["US-2.2", "US-2.3"])
    second = view("merge", ["US-2.1", "US-2.2"])
    other = view("merge", ["US-1.1", "US-1.2"])

    items = items_by_key(export(first, second, other))

    warnings = items[first["key"]]["warnings"] + items[second["key"]]["warnings"]
    assert any("US-2.3" in w and "US-2.1" in w for w in warnings), warnings
    assert items[other["key"]]["warnings"] == []


@pytest.mark.parametrize(
    ("decision", "instruction"),
    [
        (
            view("merge", ["US-1.1", "US-1.2"]),
            DO_LINES["merge"].format(drop="US-1.2", drop_title="Profile edits", keep="US-1.1"),
        ),
        (view("split", ["US-3.1"]), DO_LINES["split"].format(story="US-3.1")),
        (view("overlap", ["US-1.3", "US-2.2"]), DO_LINES["untangle"]),
    ],
    ids=["merge", "split", "untangle"],
)
def test_items_carry_stories_and_the_fixed_do_line(
    decision: DecisionView, instruction: str
) -> None:
    """§7.4: each item has its stories (id, title, hash, text) and its fixed Do line."""
    [item] = export(decision)["items"]

    assert item["instruction"] == instruction
    assert sorted(s["id"] for s in item["stories"]) == sorted(decision["stories"])
    for story in item["stories"]:
        assert story["title"] == TITLE[story["id"]]
        assert story["text_hash"] == HASH[story["id"]] == text_hash(story["text"])


# --- Markdown -------------------------------------------------------------------------------------


def story_json(sid: str) -> dict[str, str]:
    return {"id": sid, "title": TITLE[sid], "text_hash": HASH[sid], "text": TEXT[sid]}


PLANTED_DOC: dict[str, Any] = {
    "format": EXPORT_FORMAT,
    "version": 1,
    "run": {"ref": REF, "snapshot_sha256": SNAP},
    "items": [
        {
            "order": 1,
            "key": "merge:US-1.1>US-1.2",
            "action": "merge",
            "stories": [story_json("US-1.1"), story_json("US-1.2")],
            "note": "keep the photo rule",
            "evidence": {
                "decisive_value": 0.91,
                "borderline": False,
                "relation": "duplicate",
                "m": 0.91,
                "o": 0.97,
            },
            "status": "current",
            "warnings": [],
            "instruction": DO_LINES["merge"].format(
                drop="US-1.2", drop_title="Profile edits", keep="US-1.1"
            ),
        },
        {
            "order": 2,
            "key": "split:US-3.1",
            "action": "split",
            "stories": [story_json("US-3.1")],
            "note": "",
            "evidence": {"decisive_value": 0.86, "borderline": True, "bundled": 0.86},
            "status": "current",
            "warnings": ["Check US-3.1 with its owner before splitting"],
            "instruction": DO_LINES["split"].format(story="US-3.1"),
        },
        {
            "order": 3,
            "key": "overlap:US-1.3+US-2.2",
            "action": "untangle",
            "stories": [story_json("US-1.3"), story_json("US-2.2")],
            "note": "draw the line at alerts",
            "evidence": {"decisive_value": 0.74, "mean_o": 0.74},
            "status": "current",
            "warnings": [],
            "instruction": DO_LINES["untangle"],
        },
    ],
}


def test_markdown_has_header_sections_ids_titles_hashes_and_do_lines() -> None:
    """§7.4 Markdown: run and snapshot, the intro, `## <n>. <Action>` sections with everything."""
    assert schema_errors(PLANTED_DOC) == []  # the planted input is itself a valid export

    md = export_markdown_doc(PLANTED_DOC)

    header, *sections = re.split(r"(?m)^(?=## )", md)
    assert REF in header and SNAP in header
    assert EXPORT_INTRO in header
    headings = [re.match(r"## (\d+)\. (\w+)", s) for s in sections]
    assert [(m.group(1), m.group(2)) for m in headings if m] == [
        ("1", "Merge"),
        ("2", "Split"),
        ("3", "Untangle"),
    ]
    for section, item in zip(sections, PLANTED_DOC["items"], strict=True):
        for story in item["stories"]:
            assert story["id"] in section
            assert story["title"] in section
            assert story["text_hash"] in section
        assert "**Do**" in section
        assert item["instruction"] in section
        assert item["note"] in section
        for warning in item["warnings"]:
            assert warning in section
    assert "0.91" in sections[0] and "0.97" in sections[0]
    assert "borderline" in sections[1].lower()


# --- the UI fixture end to end --------------------------------------------------------------------


@pytest.mark.parametrize("include_stale", [False, True])
def test_fixture_export_validates_and_honours_include_stale(
    tmp_path: Path, include_stale: bool
) -> None:
    """§6 E10: the seed decisions on the latest run export to schema-valid JSON and Markdown."""
    ui = load_ui_fixture()
    decisions = tmp_path / "decisions.json"
    shutil.copyfile(ui.seed, decisions)
    run = RunStore(ui.runs_root).bundle(ui.latest)
    derived = derive(ui.run_dir(ui.latest), Thresholds(**cast(dict[str, Any], run["thresholds"])))
    views = DecisionStore(decisions).views(derived, None)

    doc = build_export(views, run, include_stale=include_stale)

    assert schema_errors(doc) == []
    seed = json.loads(ui.seed.read_text("utf-8"))
    accepted = {key for key, d in seed["decisions"].items() if d["action"] == "accept"}
    keys = {item["key"] for item in doc["items"]}
    assert keys <= accepted
    stale = accepted & set(ui.manifest["seed_decisions"]["stale_keys"])
    assert (stale <= keys) if include_stale else stale.isdisjoint(keys)
    md = export_markdown_doc(doc)
    assert EXPORT_INTRO in md
    for item in doc["items"]:
        assert all(story["id"] in md for story in item["stories"])
