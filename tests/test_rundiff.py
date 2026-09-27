"""diff_runs: stories added, removed, edited, renamed; proposals new, gone, changed.

Spec: docs/ui/DESIGN.md §4.3 and §7.3 `RunDiff`; the UI fixture's expectations are in its
manifest.json (tests/fixtures/ui/MANIFEST.md). `diff_runs(a, b)` sees two derived runs and no
decisions, so `decisions_to_review` (the manifest lists the seed decisions naming a changed story)
is filled from the decisions store by the API and is not compared here. Lists are sorted, story
texts are the rendered full texts, and a rename is not also an addition and a removal.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tesselint.derive import Derived, derive
from tesselint.models import ProposalKind, Story, Thresholds
from tesselint.proposals import Proposal
from tesselint.render import render
from tesselint.server import diff_runs
from tesselint.server.payloads import RunDiff
from tests import ui_fixture

K = ProposalKind
DIFF_KEYS = ("added", "removed", "edited", "renamed", "new", "gone", "changed")
BUILT = ("runs", "source.v1.json", "decisions.seed.json", "manifest.json")


def as_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def story(sid: str, core: str) -> Story:
    return Story(sid, "E1", f"Story {sid[-1]}", "MVP", core, ("A detail line.",), 1)


def proposal(key: str, value: float, borderline: bool = False) -> Proposal:
    """A proposal named by its key (`merge:keep>drop`, `split:id`, `overlap:a+b`)."""
    kind, _, rest = key.partition(":")
    stories = tuple(rest.split(">")) if kind == "merge" else tuple(rest.split("+"))
    target = stories[0] if kind == "merge" else None
    return Proposal(
        id=f"{kind[0].upper()}1",
        kind=ProposalKind(kind),
        target=target,
        stories=stories,
        decisive_value=value,
        borderline=borderline,
        evidence=(),
        key=key,
    )


def derived(stories: tuple[Story, ...], proposals: tuple[Proposal, ...] = ()) -> Derived:
    return Derived(
        stories=stories,
        verdicts={},
        clusters=(),
        atomic={s.id: None for s in stories},
        proposals=proposals,
        thresholds=Thresholds(),
    )


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


BASE = (
    story("US-1.1", "I plan the season."),
    story("US-1.2", "I see sowing dates."),
    story("US-1.3", "I join a watering rota."),
    story("US-1.4", "I book a skip slot."),
)


def test_the_fixture_diff_equals_the_manifest() -> None:
    """E8's source of truth: the fixture's two runs, derived at their own thresholds."""
    expected = ui_fixture.manifest()["diff"]
    runs = []
    for ref in (expected["a"], expected["b"]):
        run_dir = ui_fixture.run_dir(ref)
        meta = json.loads((run_dir / "run.json").read_text("utf-8"))
        runs.append(derive(run_dir, Thresholds(**meta["thresholds"])))

    got = as_json(diff_runs(*runs))

    assert set(got) == set(RunDiff.__annotations__)
    assert {k: got[k] for k in DIFF_KEYS} == {k: expected[k] for k in DIFF_KEYS}


def test_stories_added_removed_edited_and_renamed() -> None:
    """§4.3: by ID; renamed = a removed story's text under a new ID."""
    edited = story("US-1.2", "I see sowing dates and frost warnings.")
    renamed = Story("US-1.9", "E1", BASE[2].title, "MVP", BASE[2].core, BASE[2].subs, 9)
    added = story("US-1.5", "I swap a watering day.")
    b = (BASE[0], edited, renamed, added)

    got = as_json(diff_runs(derived(BASE), derived(b)))

    assert {k: got[k] for k in ("added", "removed", "renamed")} == {
        "added": ["US-1.5"],
        "removed": ["US-1.4"],
        "renamed": [["US-1.3", "US-1.9"]],
    }
    assert got["edited"] == [
        {"id": "US-1.2", "old_text": render(BASE[1]), "new_text": render(edited)}
    ]
    assert (got["new"], got["gone"], got["changed"]) == ([], [], [])


def test_proposals_new_gone_and_changed() -> None:
    """§4.3: by key; changed when |Δ decisive value| >= 0.05 or borderline flips."""
    a = (
        proposal("merge:US-1.1>US-1.2", 0.70),
        proposal("split:US-1.3", 0.85),
        proposal("overlap:US-1.1+US-1.4", 0.72),
        proposal("merge:US-1.4>US-1.3", 0.90),
    )
    b = (
        proposal("merge:US-1.1>US-1.2", 0.74),  # |Δ| 0.04: unchanged
        proposal("split:US-1.3", 0.86, borderline=True),  # borderline flips
        proposal("overlap:US-1.1+US-1.4", 0.78),  # |Δ| 0.06
        proposal("merge:US-1.3>US-1.4", 0.80),  # the other direction: a new key
    )

    got = as_json(diff_runs(derived(BASE, a), derived(BASE, b)))

    assert got["new"] == ["merge:US-1.3>US-1.4"]
    assert got["gone"] == ["merge:US-1.4>US-1.3"]
    assert got["changed"] == ["overlap:US-1.1+US-1.4", "split:US-1.3"]
    assert (got["added"], got["removed"], got["edited"], got["renamed"]) == ([], [], [], [])


def test_a_run_against_itself_has_no_difference() -> None:
    run = derived(BASE, (proposal("merge:US-1.1>US-1.2", 0.7),))

    got = as_json(diff_runs(run, run))

    assert {k: got[k] for k in DIFF_KEYS} == {k: [] for k in DIFF_KEYS}


@pytest.mark.slow
def test_the_fixture_rebuilds_byte_for_byte(tmp_path: Path) -> None:
    """DESIGN §6: rebuilding the fixture writes exactly the committed bytes."""
    ui_fixture.manifest()  # skips when the fixture was never built
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENROUTER_")}
    env |= {"HF_HUB_OFFLINE": "1", "PYTHON_DOTENV_DISABLED": "1"}

    done = subprocess.run(
        [sys.executable, str(ui_fixture.BUILDER), "--out", str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )

    assert done.returncode == 0, done.stderr[-2000:]
    committed = {k: v for k, v in tree(ui_fixture.UI_DIR).items() if k.startswith(BUILT)}
    assert tree(tmp_path) == committed
