"""Run index: refs, collisions, depth, symlinks, kind and dry inference, latest and previous.

Spec: docs/ui/DESIGN.md §4.2 (and §2 Runs). A run directory is one holding a run.json; planted
runs here hold only that file unless a test needs a full run (then it copies the UI fixture's).
`latest` is only exposed by `GET /api/runs` and `previous` by the run bundle, so those rules are
checked through `create_app` and `RunStore`.
"""

import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from tesselint.models import Thresholds
from tesselint.server import RunStore, ServeConfig, create_app, scan_runs
from tesselint.server.runindex import MAX_DEPTH, REF_PATTERN, RunEntry
from tests import ui_fixture

PORT = 8765


def run_meta(started_at: str = "2026-09-26T14:12:29+00:00", **fields: Any) -> dict[str, Any]:
    """A complete, non-dry run.json (DESIGN §9 of docs/DESIGN.md); `fields` override or add."""
    meta: dict[str, Any] = {
        "run_id": "20260926-141229",
        "started_at": started_at,
        "finished_at": started_at,
        "wall_s": 60.0,
        "status": "complete",
        "snapshot_sha256": "5" * 64,
        "tier": "t1",
        "epics": ["E1", "E2", "E20"],
        "n_stories": 35,
        "n_tombstones": 0,
        "n_pairs": 595,
        "render": "full",
        "jobs": {"fwd": 595, "swap": 120, "atomic": 30},
        "calls": {"live": 700, "cached": 45, "failed": 0, "retries": 0, "by_status": {}},
        "cost_usd": {
            "live": 0.12,
            "cached_equivalent": 0.01,
            "preflight_estimate": 0.13,
            "key_usage_delta": 0.12,
        },
        "thresholds": asdict(Thresholds()),
        "proposals": {"merge": 3, "overlap": 1, "split": 1, "leave": 30},
    }
    return meta | fields


def write_run(root: Path, rel: str, **fields: Any) -> Path:
    """Plant `root/rel/run.json`; `fields` go into run_meta."""
    run_dir = root / rel
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(run_meta(**fields), indent=2), encoding="utf-8")
    return run_dir


def dated(day: int, **fields: Any) -> dict[str, Any]:
    """Fields of a run started on 2026-09-<day> at 09:00 UTC."""
    when = {"started_at": f"2026-09-{day:02d}T09:00:00+00:00", "run_id": f"202609{day:02d}-090000"}
    return when | fields


def by_ref(entries: list[RunEntry]) -> dict[str, RunEntry]:
    return {e.ref: e for e in entries}


def api_runs(runs_root: Path, state_dir: Path) -> dict[str, Any]:
    """`GET /api/runs` of a server on `runs_root`."""
    cfg = ServeConfig(
        host="127.0.0.1",
        port=PORT,
        runs_root=runs_root,
        state_dir=state_dir,
        sources=(),
        cli=(sys.executable, "-m", "tesselint"),
        ui_max_usd=1.0,
        token="t" * 43,
    )
    with TestClient(create_app(cfg), base_url=f"http://127.0.0.1:{PORT}") as client:
        response = client.get("/api/runs")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- refs -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "ok"),
    [
        ("20260926-141229", True),
        ("t1~20260926-141229", True),
        ("ui~mini~x.y_z-1~20260101-090000", True),
        ("-a~_b", True),
        ("a~b~c~d~e", False),  # deeper than the scan
        ("..", False),
        ("a~..~b", False),
        ("%2e%2e", False),
        (".hidden", False),
        ("/etc", False),
        ("a/b", False),
        ("a~", False),
        ("~a", False),
        ("a b", False),
        ("", False),
    ],
)
def test_ref_pattern(ref: str, ok: bool) -> None:
    """§4.2: a ref is at most four `~`-joined segments that cannot start with a dot."""
    assert (REF_PATTERN.fullmatch(ref) is not None) is ok


def test_refs_are_run_dirs_with_tilde_for_slash(tmp_path: Path) -> None:
    """§4.2: a ref is the run dir under the runs root with `/` -> `~`; meta is its run.json."""
    rels = ["20260926-141229", "t1/20260926-150000", "ui/mini/x/20260926-160000"]
    dirs = [write_run(tmp_path, rel) for rel in rels]

    entries = by_ref(scan_runs(tmp_path))

    assert sorted(entries) == sorted(rel.replace("/", "~") for rel in rels)
    for run_dir, rel in zip(dirs, rels, strict=True):
        entry = entries[rel.replace("/", "~")]
        assert entry.path == run_dir
        assert dict(entry.meta) == json.loads((run_dir / "run.json").read_text("utf-8"))
        assert REF_PATTERN.fullmatch(entry.ref)


def test_the_same_run_id_under_two_parents_is_two_runs(tmp_path: Path) -> None:
    """§4.2: refs are the identity because run IDs collide (t1/ and dry/ hold the same ID)."""
    write_run(tmp_path, "t1/20260926-141229")
    write_run(tmp_path, "dry/20260926-141229", dry_run=True)

    refs = sorted(e.ref for e in scan_runs(tmp_path))

    assert refs == ["dry~20260926-141229", "t1~20260926-141229"]


def test_the_scan_stops_at_depth_4(tmp_path: Path) -> None:
    """§4.2: os.walk to depth 4, the deepest a ref can name."""
    write_run(tmp_path, "a/b/c/20260926-141229")
    write_run(tmp_path, "a/b/c/d/20260926-141229")

    assert MAX_DEPTH == 4
    assert [e.ref for e in scan_runs(tmp_path)] == ["a~b~c~20260926-141229"]


def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    """§4.2: os.walk(followlinks=False): a linked run, or a run under a linked dir, is not seen."""
    root = tmp_path / "runs"
    write_run(root, "t1/20260926-141229")
    outside = write_run(tmp_path / "elsewhere", "t3/20260926-150000")
    (root / "linked-parent").symlink_to(outside.parent, target_is_directory=True)
    (root / "linked-run").symlink_to(outside, target_is_directory=True)

    assert [e.ref for e in scan_runs(root)] == ["t1~20260926-141229"]


def test_dirs_that_cannot_be_refs_are_skipped(tmp_path: Path) -> None:
    """§4.2: only directories whose ref matches the pattern are runs the API can name."""
    write_run(tmp_path, "t1/20260926-141229")
    write_run(tmp_path, "with space/20260926-141229")
    write_run(tmp_path, ".hidden/20260926-141229")

    assert [e.ref for e in scan_runs(tmp_path)] == ["t1~20260926-141229"]


# --- kind and dry runs ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "fields", "kind", "inferred"),
    [
        pytest.param("t1/r", {"kind": "real"}, "real", False, id="explicit-real"),
        pytest.param("t1/r", {"kind": "synthetic"}, "synthetic", False, id="explicit-synthetic"),
        pytest.param("synth/r", {"kind": "real"}, "real", False, id="explicit-beats-segment"),
        pytest.param("synth/r", {}, "synthetic", True, id="legacy-synth-segment"),
        pytest.param("x/synth/r", {}, "synthetic", True, id="legacy-inner-synth-segment"),
        pytest.param("t1/r", {}, "real", True, id="legacy-real"),
    ],
)
def test_kind(tmp_path: Path, rel: str, fields: dict[str, Any], kind: str, inferred: bool) -> None:
    """§4.2: run.json `kind` when present; a legacy run is synthetic under a `synth` segment."""
    write_run(tmp_path, rel, **fields)

    [entry] = scan_runs(tmp_path)

    assert (entry.kind, entry.kind_inferred) == (kind, inferred)


LEGACY_CALLS = {"live": 0, "cached": 0, "failed": 0, "retries": 0, "by_status": {}}


@pytest.mark.parametrize(
    ("fields", "dry"),
    [
        pytest.param({"dry_run": True}, True, id="explicit-dry"),
        pytest.param({"dry_run": False, "calls": LEGACY_CALLS}, False, id="explicit-not-dry"),
        pytest.param({"calls": LEGACY_CALLS}, True, id="legacy-no-calls"),
        pytest.param({"calls": LEGACY_CALLS | {"live": 5}}, False, id="legacy-live-calls"),
        pytest.param({"calls": LEGACY_CALLS | {"cached": 5}}, False, id="legacy-cached-only"),
        pytest.param(
            {"calls": LEGACY_CALLS, "jobs": {"fwd": 0, "swap": 0, "atomic": 0}},
            False,
            id="legacy-nothing-to-ask",
        ),
    ],
)
def test_dry_run_detection(tmp_path: Path, fields: dict[str, Any], dry: bool) -> None:
    """§4.2: run.json `dry_run`, else dry when calls.live + calls.cached == 0 and jobs.fwd > 0."""
    write_run(tmp_path, "t1/20260926-141229", **fields)

    [entry] = scan_runs(tmp_path)

    assert entry.dry_run is dry


# --- latest and previous --------------------------------------------------------------------------


def test_entries_are_newest_first_and_hide_dry_runs_unless_asked(tmp_path: Path) -> None:
    """§2 Runs: dry runs only with include_dry."""
    write_run(tmp_path, "t1/20260901-090000", **dated(1, kind="real", dry_run=False))
    write_run(tmp_path, "dry/20260902-090000", **dated(2, kind="real", dry_run=True))
    write_run(tmp_path, "t1/20260903-090000", **dated(3, kind="real", dry_run=False))
    store = RunStore(tmp_path)

    assert [e.ref for e in store.entries()] == ["t1~20260903-090000", "t1~20260901-090000"]
    assert [e.ref for e in store.entries(include_dry=True)] == [
        "t1~20260903-090000",
        "dry~20260902-090000",
        "t1~20260901-090000",
    ]


def test_latest_is_the_newest_non_dry_complete_real_run(tmp_path: Path) -> None:
    """§4.2 Latest; `GET /api/runs` lists no dry run by default."""
    root = tmp_path / "runs"
    write_run(root, "t3/20260901-090000", **dated(1, kind="real", dry_run=False, tier="t3"))
    write_run(root, "t1/20260902-090000", **dated(2, kind="real", dry_run=False))
    write_run(root, "synth/20260903-090000", **dated(3, kind="synthetic", dry_run=False))
    write_run(root, "dry/20260904-090000", **dated(4, kind="real", dry_run=True))
    write_run(root, "t3/20260905-090000", **dated(5, kind="real", status="partial_cap"))

    body = api_runs(root, tmp_path / "state")

    assert body["latest"] == "t1~20260902-090000"
    assert "dry~20260904-090000" not in [r["ref"] for r in body["runs"]]


def test_latest_is_the_newest_synthetic_run_when_there_is_no_real_one(tmp_path: Path) -> None:
    """MANIFEST.md: the fixture's runs are all synthetic and its latest is the newer one."""
    root = tmp_path / "runs"
    write_run(root, "mini/20260901-090000", **dated(1, kind="synthetic", dry_run=False))
    write_run(root, "mini/20260902-090000", **dated(2, kind="synthetic", dry_run=False))

    assert api_runs(root, tmp_path / "state")["latest"] == "mini~20260902-090000"


def test_previous_is_the_newest_earlier_run_of_the_same_kind_and_tier(tmp_path: Path) -> None:
    """§4.2 Previous: kind and tier must match; a later run is never previous."""
    manifest = ui_fixture.manifest()
    source = ui_fixture.run_dir(manifest["previous"])
    root = tmp_path / "runs"
    plan = [
        ("a/20260901-090000", dated(1, kind="real", tier="t1")),
        ("b/20260902-090000", dated(2, kind="synthetic", tier="t1")),
        ("c/20260903-090000", dated(3, kind="real", tier="t3")),
        ("d/20260904-090000", dated(4, kind="real", tier="t1")),
        ("e/20260905-090000", dated(5, kind="real", tier="t1")),
    ]
    for rel, fields in plan:
        shutil.copytree(source, root / rel)
        meta = json.loads((root / rel / "run.json").read_text("utf-8")) | fields
        meta["finished_at"] = meta["started_at"]
        (root / rel / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    store = RunStore(root)

    assert store.bundle("d~20260904-090000")["previous"] == "a~20260901-090000"
    assert store.bundle("a~20260901-090000")["previous"] is None
    assert store.bundle("b~20260902-090000")["previous"] is None


def test_the_fixture_latest_and_previous_match_the_manifest(tmp_path: Path) -> None:
    """MANIFEST.md `latest` and `previous`, read back through the API and the bundle."""
    manifest = ui_fixture.manifest()

    body = api_runs(ui_fixture.RUNS_ROOT, tmp_path / "state")

    assert body["latest"] == manifest["latest"]
    assert sorted(r["ref"] for r in body["runs"]) == sorted(manifest["runs"])
    bundle = RunStore(ui_fixture.RUNS_ROOT).bundle(manifest["latest"])
    assert bundle["previous"] == manifest["previous"]
