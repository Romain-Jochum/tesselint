"""`tesselint serve` JSON API (docs/ui/DESIGN.md §7.2 routes, §7.3 shapes, §3, §4.4).

Shapes are checked against the Python twins of §7.3 in `tesselint.server.payloads`; expected
counts come from the UI fixture manifest (tests/fixtures/ui/MANIFEST.md). Tests that need the
fixture skip until `tests/fixtures/ui/build_fixture.py` has written `manifest.json`.

This module also holds the helpers the other server test modules import: the fixture loader, the
app and client builders, raw ASGI requests and the loopback socket guard.
"""

import hashlib
import ipaddress
import json
import math
import re
import shutil
import socket
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import jsonschema
import pytest
from starlette.testclient import TestClient
from starlette.types import Message

import tesselint
from tesselint.models import Thresholds
from tesselint.protocol import KeyStatus
from tesselint.render import text_hash
from tesselint.server import ServeConfig, create_app
from tesselint.server.app import STATIC_DIR
from tesselint.server.errors import ErrorCode
from tesselint.server.export import EXPORT_INTRO
from tesselint.server.guard import TOKEN_HEADER
from tesselint.server.payloads import (
    ClusterRow,
    DecisionsResponse,
    DecisionView,
    Edge,
    Estimate,
    Health,
    Job,
    MatrixPacked,
    MergeDetails,
    OverlapDetails,
    PairDetail,
    ProposalsResponse,
    RunBundle,
    RunDiff,
    RunsResponse,
    RunSummary,
    SourceInfo,
    SplitDetails,
    StoryRow,
)
from tests import ui_fixture
from tests.conftest import SAMPLE_BACKLOG
from tests.fake_cli import (
    fake_cli_argv,
    invocations,
    is_job_run,
    kill_leftovers,
    spawn_violations,
    wait_for,
)

REPO = Path(__file__).resolve().parents[1]
# Any Markdown backlog will do for the server: the public synthetic one keeps these tests in CI.
MD_SOURCE = SAMPLE_BACKLOG
EXPORT_SCHEMA = REPO / "schemas" / "decisions-export.v1.schema.json"

TOKEN = "k3Y9-tEsT_" + "Zq7xW2" * 5 + "Ab"  # shaped like secrets.token_urlsafe(32)
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
BASE = f"http://{HOST}"
CANARY = "sk-or-canary-5f0c3e9a1b7d"
RELATIONS = {
    "duplicate",
    "A_contains_B",
    "B_contains_A",
    "partial_overlap",
    "same_area_distinct",
    "unrelated",
}
KEY_RE = r"^(merge:[^>+:]+>[^>+:]+|split:[^>+:]+|overlap:[^>+:]+(\+[^>+:]+)+)$"
FAKE_KEY_STATUS: KeyStatus = {
    "present": True,
    "usage_usd": 0.88,
    "limit_usd": 2.0,
    "remaining_usd": 1.12,
    "error": None,
}


# --- helpers shared by the server test modules ----------------------------------------------------


@dataclass(frozen=True)
class UiFixture:
    """The built UI fixture (tests/ui_fixture.py, tests/fixtures/ui/MANIFEST.md)."""

    manifest: dict[str, Any]
    runs_root: Path = ui_fixture.RUNS_ROOT
    source: Path = ui_fixture.SOURCE_V1
    seed: Path = ui_fixture.DECISIONS_SEED

    @property
    def latest(self) -> str:
        return str(self.manifest["latest"])

    @property
    def previous(self) -> str:
        return str(self.manifest["previous"])

    def run(self, ref: str) -> dict[str, Any]:
        entry: dict[str, Any] = self.manifest["runs"][ref]
        return entry

    def run_dir(self, ref: str) -> Path:
        return ui_fixture.run_dir(ref)


def ui_fixture_or_none() -> UiFixture | None:
    """The UI fixture if it has been built, else None (for tests that also run without it)."""
    if not ui_fixture.MANIFEST_PATH.is_file():
        return None
    return UiFixture(ui_fixture.manifest())


def load_ui_fixture() -> UiFixture:
    """The UI fixture; skips the calling test (ui_fixture.NOT_BUILT) when it is not built."""
    return UiFixture(ui_fixture.manifest())


def make_config(
    runs_root: Path, state_dir: Path, sources: Sequence[Path] = (), *, ui_max_usd: float = 1.0
) -> ServeConfig:
    """A ServeConfig on 127.0.0.1:PORT with the fake CLI and the committed static assets."""
    state_dir.mkdir(parents=True, exist_ok=True)
    return ServeConfig(
        host="127.0.0.1",
        port=PORT,
        runs_root=runs_root,
        state_dir=state_dir,
        sources=tuple(sources),
        cli=fake_cli_argv(),
        ui_max_usd=ui_max_usd,
        token=TOKEN,
    )


def make_client(cfg: ServeConfig, *, token: bool = True) -> TestClient:
    """A TestClient sending `Host: 127.0.0.1:PORT` (and the token unless `token=False`)."""
    headers = {TOKEN_HEADER: TOKEN} if token else {}
    return TestClient(create_app(cfg), base_url=BASE, headers=headers)


def assert_error(resp: httpx.Response, status: int, code: ErrorCode) -> None:
    """`resp` is `{"error": {"code", "message"}}` with this status and code (§7.2)."""
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code.value


def assert_shape(obj: Any, shape: Any) -> None:
    """`obj` is a JSON object with exactly the keys of the TypedDict `shape` (§7.3)."""
    name = shape.__name__
    assert isinstance(obj, dict), f"{name}: expected an object, got {obj!r}"
    required, optional = set(shape.__required_keys__), set(shape.__optional_keys__)
    assert required <= obj.keys(), f"{name} lacks {sorted(required - obj.keys())}"
    assert obj.keys() <= required | optional, f"{name} has extra {sorted(obj.keys() - required)}"


def tree_digest(*paths: Path) -> dict[str, str]:
    """sha256 of every file under `paths` (a file stands for itself), keyed by path."""
    digest: dict[str, str] = {}
    for root in paths:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in files:
            digest[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def raw_request(
    client: TestClient,
    method: str,
    raw_path: bytes,
    headers: Sequence[tuple[str, str]] = (("host", HOST),),
) -> RawResponse:
    """Send `raw_path` byte for byte, as uvicorn passes it (httpx would normalise `..` away).

    `client` must be open (`with make_client(...) as client`) so the app's lifespan has run.
    """
    portal = client.portal
    assert portal is not None, "open the TestClient with `with` first"
    return portal.call(_raw_asgi, client, method, raw_path, headers)


async def _raw_asgi(
    client: TestClient, method: str, raw_path: bytes, headers: Sequence[tuple[str, str]]
) -> RawResponse:
    path, _, query = raw_path.partition(b"?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": unquote(path.decode("latin-1")),
        "raw_path": path,
        "query_string": query,
        "root_path": "",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", PORT),
        "state": dict(client.app_state),
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await client.app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    hdrs = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start["headers"]}
    return RawResponse(status=int(start["status"]), headers=hdrs, body=body)


def install_socket_guard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every non-loopback connect or name lookup fail; return the list of attempts."""
    attempts: list[str] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def allowed(family: int, address: Any) -> bool:
        if family == getattr(socket, "AF_UNIX", object()):
            return True
        return _is_loopback(address[0] if isinstance(address, tuple) else address)

    def connect(self: socket.socket, address: Any) -> None:
        if not allowed(self.family, address):
            attempts.append(repr(address))
            raise OSError(f"socket guard: non-loopback connect to {address!r}")
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if not allowed(self.family, address):
            attempts.append(repr(address))
            raise OSError(f"socket guard: non-loopback connect to {address!r}")
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and not _is_loopback(host):
            attempts.append(repr(host))
            raise socket.gaierror(f"socket guard: lookup of {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return attempts


def _is_loopback(host: Any) -> bool:
    text = host.decode() if isinstance(host, bytes) else str(host)
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text.strip("[]")).is_loopback
    except ValueError:
        return False


def matrix_index(n: int, i: int, j: int) -> int:
    """§7.3: index of (i, j), i < j, in the packed upper triangle."""
    return i * n - i * (i + 1) // 2 + (j - i - 1)


def schema_errors(doc: Any) -> list[str]:
    """Every violation of schemas/decisions-export.v1.schema.json by `doc`."""
    schema = json.loads(EXPORT_SCHEMA.read_text("utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(doc)]


# --- fixtures -------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts = install_socket_guard(monkeypatch)
    yield
    assert attempts == [], f"the server tried to reach the network: {attempts}"


@pytest.fixture(scope="module", autouse=True)
def _fixture_untouched() -> Iterator[None]:
    """§1, §6 security: the whole API session writes no source and no run directory."""
    ui = ui_fixture_or_none()
    before = tree_digest(ui.runs_root, ui.source, ui.seed) if ui else {}
    yield
    if ui is not None:
        assert tree_digest(ui.runs_root, ui.source, ui.seed) == before


@pytest.fixture
def fake_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    log = tmp_path / "fake_cli.log"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setenv("FAKE_CLI_MODE", "ok")
    yield log
    kill_leftovers(log)
    assert spawn_violations(log) == []


@pytest.fixture
def ui() -> UiFixture:
    return load_ui_fixture()


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    path = tmp_path / "state"
    path.mkdir()
    return path


@pytest.fixture
def api(ui: UiFixture, state_dir: Path, fake_log: Path) -> Iterator[TestClient]:
    with make_client(make_config(ui.runs_root, state_dir, (ui.source,))) as client:
        yield client


@pytest.fixture
def seeded_api(ui: UiFixture, state_dir: Path, fake_log: Path) -> Iterator[TestClient]:
    """The fixture app with `decisions.seed.json` as the decisions file."""
    shutil.copyfile(ui.seed, state_dir / "decisions.json")
    with make_client(make_config(ui.runs_root, state_dir, (ui.source,))) as client:
        yield client


@pytest.fixture
def bare_api(tmp_path: Path, fake_log: Path) -> Iterator[TestClient]:
    """An app on an empty runs root with a Markdown source and a missing JSON source."""
    runs = tmp_path / "runs"
    runs.mkdir()
    source = tmp_path / "stories.md"
    shutil.copyfile(MD_SOURCE, source)
    cfg = make_config(runs, tmp_path / "state", (source, tmp_path / "missing.json"))
    with make_client(cfg) as client:
        yield client


def get_json(client: TestClient, url: str, **params: str) -> Any:
    resp = client.get(url, params=params or None)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- read API -------------------------------------------------------------------------------------


def test_health_reports_version_build_sources_and_cap(bare_api: TestClient, tmp_path: Path) -> None:
    """§7.2 `/api/health`: version, the build stamp, allowlisted sources by id, ui_max_usd."""
    body = get_json(bare_api, "/api/health")

    assert_shape(body, Health)
    assert body["version"] == tesselint.__version__
    stamp = json.loads((STATIC_DIR / "build.json").read_text("utf-8"))
    assert body["build"] == stamp["source_hash"]
    assert body["ui_max_usd"] == 1.0
    for source in body["sources"]:
        assert_shape(source, SourceInfo)
    first, second = body["sources"][:2]
    assert (first["id"], first["format"], first["exists"]) == (0, "md", True)
    assert Path(first["path"]).resolve() == (tmp_path / "stories.md").resolve()
    assert (second["id"], second["format"], second["exists"]) == (1, "json", False)


def test_runs_lists_fixture_runs_newest_first(ui: UiFixture, api: TestClient) -> None:
    """§4.2, §7.2 `/api/runs`: every run with its summary; latest per the manifest."""
    body = get_json(api, "/api/runs")

    assert_shape(body, RunsResponse)
    assert body["latest"] == ui.latest
    refs = [summary["ref"] for summary in body["runs"]]
    assert sorted(refs) == sorted(ui.manifest["runs"])
    assert refs[0] == ui.latest
    for summary in body["runs"]:
        assert_shape(summary, RunSummary)
        expected = ui.run(summary["ref"])
        assert summary["kind"] == expected["kind"]
        assert summary["kind_inferred"] is False
        assert summary["dry_run"] is False
        assert summary["n_stories"] == expected["n_stories"]
        assert summary["proposals"] == expected["proposals"]


@pytest.mark.parametrize("role", ["latest", "previous"])
def test_run_bundle_matches_manifest(ui: UiFixture, api: TestClient, role: str) -> None:
    """§7.3 RunBundle: counts, document order, text hashes, thresholds, previous run."""
    ref = ui.manifest[role]
    expected = ui.run(ref)

    body = get_json(api, f"/api/runs/{ref}")

    assert_shape(body, RunBundle)
    assert_shape(body["run"], RunSummary)
    assert body["run"]["ref"] == ref
    assert len(body["stories"]) == expected["n_stories"]
    assert len(body["epics"]) == expected["n_epics"]
    assert len(body["tombstones"]) == expected["n_tombstones"]
    for story in body["stories"]:
        assert_shape(story, StoryRow)
        assert story["text_hash"] == text_hash(story["text"])
    assert [s["order"] for s in body["stories"]] == sorted(s["order"] for s in body["stories"])
    for cluster in body["clusters"]:
        assert_shape(cluster, ClusterRow)
        for edge in cluster["edges"]:
            assert_shape(edge, Edge)
    assert set(body["thresholds"]) == {f.name for f in fields(Thresholds)}
    assert body["previous"] == (ui.previous if role == "latest" else None)


def test_latest_bundle_reports_source_edits(ui: UiFixture, api: TestClient) -> None:
    """§2 "What changed" vs the current source: source.v1.json is one edit past the latest run."""
    status = get_json(api, f"/api/runs/{ui.latest}")["source_status"]

    expected = ui.manifest["source_vs_latest"]
    assert status["source_id"] == 0
    assert status["same_snapshot"] is False
    assert sorted(status["edited"]) == sorted(expected["edited"])
    assert sorted(status["added"]) == sorted(expected["added"])
    assert sorted(status["removed"]) == sorted(expected["removed"])


@pytest.mark.parametrize("role", ["latest", "previous"])
def test_matrix_is_packed_and_agrees_with_pair_detail(
    ui: UiFixture, api: TestClient, role: str
) -> None:
    """§7.3 MatrixPacked (upper triangle, document order) and PairDetail match the manifest."""
    ref = ui.manifest[role]
    pair = ui.run(ref)["pair"]
    ids = [s["id"] for s in get_json(api, f"/api/runs/{ref}")["stories"]]

    matrix = get_json(api, f"/api/runs/{ref}/matrix")
    detail = get_json(api, f"/api/runs/{ref}/pairs/{pair['a']}/{pair['b']}")

    assert_shape(matrix, MatrixPacked)
    n = len(ids)
    assert matrix["ids"] == ids
    assert len(matrix["o"]) == len(matrix["m"]) == len(matrix["rel"]) == n * (n - 1) // 2
    assert sorted(matrix["rel_order"]) == sorted(RELATIONS)
    for value in (*matrix["o"], *matrix["m"]):
        assert value is None or (math.isfinite(value) and 0.0 <= value <= 1.0)
    assert all(r is None or 0 <= r < len(RELATIONS) for r in matrix["rel"])
    i, j = sorted((ids.index(pair["a"]), ids.index(pair["b"])))
    k = matrix_index(n, i, j)
    assert matrix["o"][k] == pytest.approx(pair["overlap"])
    assert matrix["m"][k] == pytest.approx(pair["merge"])
    assert_shape(detail, PairDetail)
    assert detail["overlap"] == pytest.approx(pair["overlap"])
    assert detail["merge"] == pytest.approx(pair["merge"])
    assert detail["relation"] == pair["relation"]


@pytest.mark.parametrize("role", ["latest", "previous"])
def test_proposals_and_cases_match_manifest(ui: UiFixture, api: TestClient, role: str) -> None:
    """§4.3, §7.3: proposals by kind with keys and details; one case per group, split, overlap."""
    ref = ui.manifest[role]
    expected = ui.run(ref)

    body = get_json(api, f"/api/runs/{ref}/proposals")

    assert_shape(body, ProposalsResponse)
    assert dict(Counter(p["kind"] for p in body["proposals"])) == {
        kind: n for kind, n in expected["proposals"].items() if n
    }
    details = {"merge": MergeDetails, "overlap": OverlapDetails, "split": SplitDetails}
    for proposal in body["proposals"]:
        assert_shape(proposal["details"], details[proposal["kind"]])
        assert re.fullmatch(KEY_RE, proposal["key"]), proposal["key"]
        assert proposal["key"].startswith(proposal["kind"] + ":")
    cases = Counter(case["kind"] for case in body["cases"])
    assert len(body["cases"]) == expected["cases"]["total"]
    assert cases["merge"] == expected["cases"]["merge_groups"]
    assert cases["split"] == expected["cases"]["split"]
    assert cases["overlap"] == expected["cases"]["overlap"]
    assert sorted(hub["story"] for hub in body["hubs"]) == sorted(expected["hubs"])


def test_proposals_expose_groups_hubs_borderline_and_overlaps(
    ui: UiFixture, api: TestClient
) -> None:
    """§2 Review, §4.3: group options by m, the hub's split conflict, borderline and overlaps."""
    expected = ui.run(ui.latest)

    body = get_json(api, f"/api/runs/{ui.latest}/proposals")

    by_key = {p["key"]: p for p in body["proposals"]}
    group = next(c for c in body["cases"] if c["key"] == expected["multi_option_group"])
    assert group["proposals"] == expected["multi_option_group_options"]
    hub = next(h for h in body["hubs"] if h["story"] == expected["hub_story"])
    assert hub["split"] == expected["hub_split_key"]
    assert by_key[expected["borderline_merge_key"]]["borderline"] is True
    overlaps = sorted(key for key, p in by_key.items() if p["kind"] == "overlap")
    assert overlaps == sorted(expected["overlap_keys"])


def test_thresholds_query_rederives_without_writing(ui: UiFixture, api: TestClient) -> None:
    """§2 URL `th=`, §4.3: proposals re-derive in memory at merge 0.9; the run dir is unchanged."""
    run_dir = ui.run_dir(ui.latest)
    before = tree_digest(run_dir)

    strict = get_json(api, f"/api/runs/{ui.latest}/proposals", merge="0.9")
    default = get_json(api, f"/api/runs/{ui.latest}/proposals")

    merges = [p for p in strict["proposals"] if p["kind"] == "merge"]
    assert strict["thresholds"]["merge"] == 0.9
    assert len(merges) == ui.run(ui.latest)["merges_at_merge_0_9"]
    assert (
        sum(p["kind"] == "merge" for p in default["proposals"])
        == (ui.run(ui.latest)["proposals"]["merge"])
    )
    assert tree_digest(run_dir) == before


@pytest.mark.parametrize(
    ("endpoint", "params"),
    [
        ("proposals", {"merge": "1.5"}),
        ("proposals", {"merge": "abc"}),
        ("proposals", {"cluster": "-0.1"}),
        ("proposals", {"split": "nan"}),
        ("decisions", {"merge": "2"}),
    ],
)
def test_bad_threshold_is_422(
    ui: UiFixture, api: TestClient, endpoint: str, params: dict[str, str]
) -> None:
    """§7.2: a threshold outside [0, 1] or not a number is 422 `bad_threshold`."""
    if endpoint == "proposals":
        resp = api.get(f"/api/runs/{ui.latest}/proposals", params=params)
    else:
        resp = api.get("/api/decisions", params={"run": ui.latest, **params})

    assert_error(resp, 422, ErrorCode.BAD_THRESHOLD)


def test_diff_matches_manifest(ui: UiFixture, seeded_api: TestClient) -> None:
    """§4.3, §7.3 RunDiff between the fixture's two runs, with the seed decisions to review."""
    expected = ui.manifest["diff"]

    body = get_json(seeded_api, "/api/diff", a=expected["a"], b=expected["b"])

    assert_shape(body, RunDiff)
    for field in ("added", "removed", "new", "gone", "changed", "decisions_to_review"):
        assert sorted(body[field]) == sorted(expected[field]), field
    assert sorted(map(list, body["renamed"])) == sorted(map(list, expected["renamed"]))
    edited = [e if isinstance(e, str) else e["id"] for e in expected["edited"]]
    assert sorted(e["id"] for e in body["edited"]) == sorted(edited)
    assert all(e["old_text"] != e["new_text"] for e in body["edited"])


# --- decisions ------------------------------------------------------------------------------------


def test_seed_decisions_on_latest_have_manifest_statuses(
    ui: UiFixture, seeded_api: TestClient
) -> None:
    """§3 computed statuses: run 1's decisions shown on run 2 (E5); none is lost."""
    seed = ui.manifest["seed_decisions"]
    stored = json.loads(ui.seed.read_text("utf-8"))

    body = get_json(seeded_api, "/api/decisions", run=ui.latest)

    assert_shape(body, DecisionsResponse)
    assert body["rev"] == stored["rev"]
    views = body["decisions"]
    for view in views:
        assert_shape(view, DecisionView)
    assert len(views) == seed["total"]
    statuses = Counter(v["status"] for v in views)
    for status in ("current", "stale", "missing"):
        assert statuses[status] == seed["on_latest"][status], status
    assert sum(not v["proposed"] for v in views) == seed["on_latest"]["orphaned"]
    assert sorted(v["key"] for v in views if v["status"] == "stale") == sorted(seed["stale_keys"])
    assert sorted(v["key"] for v in views if v["status"] == "missing") == sorted(
        seed["missing_keys"]
    )
    assert all(v["changed"] for v in views if v["status"] == "stale")


def test_post_decision_fills_record_and_undo_restores(
    ui: UiFixture, seeded_api: TestClient, state_dir: Path
) -> None:
    """§7.2: the browser sends a key; the server fills hashes and titles; undo is one more rev."""
    key = ui.run(ui.latest)["borderline_merge_key"]
    before = get_json(seeded_api, "/api/decisions", run=ui.latest)
    actions_before = {v["key"]: v["action"] for v in before["decisions"]}

    resp = seeded_api.post(
        "/api/decisions",
        json={"run": ui.latest, "changes": [{"key": key, "action": "accept", "note": "same"}]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rev"] == before["rev"] + 1
    view = next(v for v in body["decisions"] if v["key"] == key)
    assert (view["action"], view["note"], view["status"], view["run"]) == (
        "accept",
        "same",
        "current",
        ui.latest,
    )
    assert set(view["text_hashes"]) == set(view["stories"]) == set(view["titles"])
    stored = json.loads((state_dir / "decisions.json").read_text("utf-8"))
    assert stored["rev"] == body["rev"]
    assert set(stored["decisions"][key]["texts"]) == set(view["stories"])

    undone = seeded_api.post("/api/decisions/undo", json={"run": ui.latest})

    assert undone.status_code == 200, undone.text
    assert undone.json()["rev"] == before["rev"] + 2
    assert {v["key"]: v["action"] for v in undone.json()["decisions"]} == actions_before


@pytest.mark.parametrize(
    "kind", ["run-local merge id", "run-local split id", "unknown", "unsorted"]
)
def test_unknown_decision_key_is_422_and_writes_nothing(
    ui: UiFixture, seeded_api: TestClient, state_dir: Path, kind: str
) -> None:
    """§3 Keys, §7.2: only proposal keys of the run; run-local IDs such as M1 are unknown."""
    members = ui.run(ui.latest)["overlap_keys"][0].removeprefix("overlap:").split("+")
    key = {
        "run-local merge id": "M1",
        "run-local split id": "S1",
        "unknown": "merge:NOPE-1>NOPE-2",
        "unsorted": "overlap:" + "+".join(reversed(members)),
    }[kind]
    before = (state_dir / "decisions.json").read_bytes()

    resp = seeded_api.post(
        "/api/decisions", json={"run": ui.latest, "changes": [{"key": key, "action": "accept"}]}
    )

    assert_error(resp, 422, ErrorCode.UNKNOWN_KEY)
    assert (state_dir / "decisions.json").read_bytes() == before


def test_undo_with_no_history_is_409(ui: UiFixture, api: TestClient) -> None:
    """§7.2: undo on an empty store is 409 `nothing_to_undo`."""
    resp = api.post("/api/decisions/undo", json={"run": ui.latest})

    assert_error(resp, 409, ErrorCode.NOTHING_TO_UNDO)


def test_export_download_and_write(ui: UiFixture, seeded_api: TestClient, state_dir: Path) -> None:
    """§2 Export, §7.2, §7.4: JSON validates, Markdown has the intro, a write makes both files."""
    as_json = seeded_api.get(
        "/api/export", params={"run": ui.latest, "format": "json", "include_stale": "0"}
    )
    as_md = seeded_api.get(
        "/api/export", params={"run": ui.latest, "format": "md", "include_stale": "1"}
    )
    written = seeded_api.post("/api/exports", json={"run": ui.latest, "include_stale": False})

    assert as_json.status_code == 200, as_json.text
    assert schema_errors(as_json.json()) == []
    assert as_md.status_code == 200, as_md.text
    assert EXPORT_INTRO in as_md.text
    assert written.status_code == 201, written.text
    paths = written.json()
    assert set(paths) == {"md_path", "json_path"}
    exports = (state_dir / "exports").resolve()
    md_path, json_path = Path(paths["md_path"]).resolve(), Path(paths["json_path"]).resolve()
    assert md_path.parent == exports and json_path.parent == exports
    assert schema_errors(json.loads(json_path.read_text("utf-8"))) == []
    assert EXPORT_INTRO in md_path.read_text("utf-8")


# --- errors without the fixture -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", "/api/runs/nope~20260101-000000", None),
        ("GET", "/api/runs/nope/matrix", None),
        ("GET", "/api/runs/nope/proposals", None),
        ("GET", "/api/runs/nope/pairs/US-1.1/US-1.2", None),
        ("GET", "/api/diff?a=nope&b=nope2", None),
        ("GET", "/api/decisions?run=nope", None),
        ("GET", "/api/export?run=nope&format=json&include_stale=0", None),
        ("POST", "/api/decisions", {"run": "nope", "changes": []}),
        ("POST", "/api/decisions/undo", {"run": "nope"}),
        ("POST", "/api/exports", {"run": "nope", "include_stale": False}),
    ],
)
def test_unknown_run_is_404(
    bare_api: TestClient, method: str, url: str, body: dict[str, Any] | None
) -> None:
    """§7.2: a ref the index does not know is 404 `run_not_found`."""
    resp = bare_api.request(method, url, json=body)

    assert_error(resp, 404, ErrorCode.RUN_NOT_FOUND)


def test_key_endpoint_relays_cli_key_status(bare_api: TestClient, fake_log: Path) -> None:
    """§4.4, §7.2 `/api/key`: the KeyStatus of `tesselint key --json`, from a subprocess."""
    body = get_json(bare_api, "/api/key")

    assert_shape(body, KeyStatus)
    assert body == FAKE_KEY_STATUS
    assert [r["argv"] for r in invocations(fake_log)] == [["key", "--json"]]


# --- jobs over HTTP -------------------------------------------------------------------------------


def post_estimate(client: TestClient) -> Any:
    resp = client.post("/api/estimates", json={"source_id": 0, "tier": "t1"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_job_lifecycle_over_http(
    bare_api: TestClient, fake_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§2 Re-run, §4.4, §7.2: estimate 201, start 202, a second start 409, cancel 202."""
    monkeypatch.setenv("FAKE_CLI_MODE", "slow")
    estimate = post_estimate(bare_api)
    assert_shape(estimate, Estimate)
    assert estimate["usd"] == 0.05
    assert estimate["max_usd_allowed"] == pytest.approx(1.0)

    started = bare_api.post(
        "/api/jobs", json={"estimate_id": estimate["id"], "max_usd": 0.1, "confirm": "spend"}
    )
    assert started.status_code == 202, started.text
    job = started.json()
    assert_shape(job, Job)
    assert (job["state"], job["max_usd"]) == ("running", 0.1)

    again = post_estimate(bare_api)
    second = bare_api.post(
        "/api/jobs", json={"estimate_id": again["id"], "max_usd": 0.1, "confirm": "spend"}
    )
    assert_error(second, 409, ErrorCode.JOB_RUNNING)

    cancelled = bare_api.post(f"/api/jobs/{job['id']}/cancel", json={})
    assert cancelled.status_code == 202, cancelled.text
    final = wait_for(
        lambda: get_json(bare_api, f"/api/jobs/{job['id']}"), lambda j: j["state"] != "running"
    )
    assert (final["state"], final["exit_code"]) == ("cancelled", 130)
    assert sum(is_job_run(r["argv"]) for r in invocations(fake_log)) == 1


@pytest.mark.parametrize("case", ["stale estimate", "cap above ui_max_usd", "no confirm"])
def test_job_start_refusals_over_http(
    bare_api: TestClient, fake_log: Path, tmp_path: Path, case: str
) -> None:
    """§4.4 start checks over HTTP: 409 `estimate_stale`, 422 `cap_invalid`, confirm required."""
    estimate = post_estimate(bare_api)
    body: dict[str, Any] = {"estimate_id": estimate["id"], "max_usd": 0.1, "confirm": "spend"}
    if case == "stale estimate":
        with (tmp_path / "stories.md").open("a", encoding="utf-8") as fh:
            fh.write("\n")
    elif case == "cap above ui_max_usd":
        body["max_usd"] = 1.5
    else:
        del body["confirm"]

    resp = bare_api.post("/api/jobs", json=body)

    if case == "stale estimate":
        assert_error(resp, 409, ErrorCode.ESTIMATE_STALE)
    elif case == "cap above ui_max_usd":
        assert_error(resp, 422, ErrorCode.CAP_INVALID)
    else:
        assert 400 <= resp.status_code < 500, resp.text
    time.sleep(0.2)
    assert not any(is_job_run(r["argv"]) for r in invocations(fake_log))
