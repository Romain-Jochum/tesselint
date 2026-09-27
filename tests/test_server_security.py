"""`tesselint serve` hardening (docs/ui/DESIGN.md §1 Boundary, §4.1 LocalGuard, §6 security).

Host, token, Origin, Sec-Fetch-Site and content-type checks; hostile refs and static traversal;
no `sk-or-` in any response, header, log line, job file or asset; the import boundary.
"""

import json
import logging
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from tesselint.server.app import STATIC_DIR
from tesselint.server.errors import ErrorCode
from tesselint.server.guard import CSP, TOKEN_HEADER
from tesselint.server.jobs import EXIT_MESSAGES
from tests.fake_cli import invocations, kill_leftovers, spawn_violations, wait_for
from tests.test_server_api import (
    CANARY,
    HOST,
    MD_SOURCE,
    PORT,
    REPO,
    TOKEN,
    assert_error,
    install_socket_guard,
    make_client,
    make_config,
    raw_request,
    ui_fixture_or_none,
)

FORBIDDEN_MODULES = ("tesselint.jev", "httpx", "dotenv")
SECRET = "sk-or-"
UNDO = "/api/decisions/undo"  # a POST that reaches the app: 404 run_not_found for run "nope"


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts = install_socket_guard(monkeypatch)
    yield
    assert attempts == [], f"the server tried to reach the network: {attempts}"


@pytest.fixture
def fake_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    log = tmp_path / "fake_cli.log"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setenv("FAKE_CLI_MODE", "ok")
    yield log
    kill_leftovers(log)
    assert spawn_violations(log) == []


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """runs/ (empty), a decoy run dir outside it, state/ and an allowlisted source."""
    (tmp_path / "runs").mkdir()
    decoy = tmp_path / "outside"
    decoy.mkdir()
    (decoy / "run.json").write_text('{"run_id": "DECOY-RUN", "status": "complete"}', "utf-8")
    shutil.copyfile(MD_SOURCE, tmp_path / "stories.md")
    return tmp_path


@pytest.fixture
def guarded(workdir: Path, fake_log: Path) -> Iterator[TestClient]:
    """The app on the empty runs root, with no default token header."""
    cfg = make_config(workdir / "runs", workdir / "state", (workdir / "stories.md",))
    with make_client(cfg, token=False) as client:
        yield client


# --- LocalGuard -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "path", "status"),
    [
        (HOST, "/api/health", 200),
        (f"localhost:{PORT}", "/api/health", 200),
        (f"[::1]:{PORT}", "/api/health", 200),
        (f"127.0.0.1:{PORT + 1}", "/api/health", 403),
        ("127.0.0.1", "/api/health", 403),
        ("localhost", "/api/health", 403),
        (f"evil.example:{PORT}", "/api/health", 403),
        (f"localhost.evil.example:{PORT}", "/api/health", 403),
        (f"127.0.0.1.nip.io:{PORT}", "/api/health", 403),
        (f"0.0.0.0:{PORT}", "/api/health", 403),
        ("", "/api/health", 403),
        (f"evil.example:{PORT}", "/", 403),  # a rebinding page must not get the token-bearing index
    ],
)
def test_host_header_must_be_loopback_with_port(
    guarded: TestClient, host: str, path: str, status: int
) -> None:
    """§4.1: Host must be 127.0.0.1:P, localhost:P or [::1]:P, else 403 `bad_host`."""
    resp = guarded.get(path, headers={"Host": host})

    if status == 403:
        assert_error(resp, 403, ErrorCode.BAD_HOST)
        assert TOKEN not in resp.text
    else:
        assert resp.status_code == status, resp.text


JSON = {"Content-Type": "application/json"}
WITH_TOKEN = {TOKEN_HEADER: TOKEN, **JSON}
BODY = b'{"run": "nope"}'


@pytest.mark.parametrize(
    ("method", "headers", "body", "status", "code"),
    [
        pytest.param("POST", WITH_TOKEN, BODY, 404, ErrorCode.RUN_NOT_FOUND, id="valid"),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Content-Type": "application/json; charset=utf-8"},
            BODY,
            404,
            ErrorCode.RUN_NOT_FOUND,
            id="json-charset",
        ),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Origin": f"http://{HOST}"},
            BODY,
            404,
            ErrorCode.RUN_NOT_FOUND,
            id="same-origin-Origin",
        ),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Sec-Fetch-Site": "same-origin"},
            BODY,
            404,
            ErrorCode.RUN_NOT_FOUND,
            id="fetch-same-origin",
        ),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Sec-Fetch-Site": "none"},
            BODY,
            404,
            ErrorCode.RUN_NOT_FOUND,
            id="fetch-none",
        ),
        pytest.param("POST", JSON, BODY, 403, ErrorCode.CSRF, id="no-token"),
        pytest.param(
            "POST", {**JSON, TOKEN_HEADER: TOKEN[:-1] + "x"}, BODY, 403, ErrorCode.CSRF, id="wrong"
        ),
        pytest.param("POST", {**JSON, TOKEN_HEADER: ""}, BODY, 403, ErrorCode.CSRF, id="empty"),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Origin": "http://evil.example"},
            BODY,
            403,
            ErrorCode.CSRF,
            id="foreign-Origin",
        ),
        pytest.param(
            "POST", {**WITH_TOKEN, "Origin": "null"}, BODY, 403, ErrorCode.CSRF, id="null-Origin"
        ),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Origin": f"http://localhost:{PORT}"},
            BODY,
            403,
            ErrorCode.CSRF,
            id="Origin-not-Host",
        ),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Origin": f"https://{HOST}"},
            BODY,
            403,
            ErrorCode.CSRF,
            id="https-Origin",
        ),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Sec-Fetch-Site": "cross-site"},
            BODY,
            403,
            ErrorCode.CSRF,
            id="fetch-cross-site",
        ),
        pytest.param(
            "POST",
            {**WITH_TOKEN, "Sec-Fetch-Site": "same-site"},
            BODY,
            403,
            ErrorCode.CSRF,
            id="fetch-same-site",
        ),
        pytest.param("PUT", JSON, BODY, 403, ErrorCode.CSRF, id="PUT-no-token"),
        pytest.param(
            "POST",
            {TOKEN_HEADER: TOKEN, "Content-Type": "text/plain"},
            BODY,
            415,
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            id="text-plain",
        ),
        pytest.param(
            "POST",
            {TOKEN_HEADER: TOKEN, "Content-Type": "application/x-www-form-urlencoded"},
            b"run=nope",
            415,
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            id="form",
        ),
        pytest.param(
            "POST",
            {TOKEN_HEADER: TOKEN},
            BODY,
            415,
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            id="no-content-type",
        ),
    ],
)
def test_non_get_needs_token_same_origin_and_json(
    guarded: TestClient,
    method: str,
    headers: dict[str, str],
    body: bytes,
    status: int,
    code: ErrorCode,
) -> None:
    """§4.1: token, Origin = http://<Host>, Sec-Fetch-Site same-origin|none, a JSON body."""
    resp = guarded.request(method, UNDO, content=body, headers=headers)

    assert_error(resp, status, code)


@pytest.mark.parametrize(
    ("site", "refused"),
    [
        pytest.param(None, False, id="no-header"),
        pytest.param("same-origin", False, id="same-origin"),
        pytest.param("none", False, id="typed-url"),
        pytest.param("same-site", True, id="same-site"),
        pytest.param("cross-site", True, id="cross-site"),
    ],
)
@pytest.mark.parametrize("path", ["/api/health", "/api/key", "/api/runs"])
def test_api_reads_refuse_other_sites(
    guarded: TestClient, path: str, site: str | None, refused: bool
) -> None:
    """A2 L3: a foreign page's GET /api/* (/api/key spawns the CLI) is refused by Sec-Fetch-Site."""
    resp = guarded.get(path, headers={} if site is None else {"Sec-Fetch-Site": site})

    if refused:
        assert_error(resp, 403, ErrorCode.CSRF)
    else:
        assert resp.status_code == 200, resp.text


def test_pages_still_open_from_a_link_on_another_site(guarded: TestClient) -> None:
    """A2 L3: only /api reads check Sec-Fetch-Site; a link from another site opens the UI."""
    resp = guarded.get("/", headers={"Sec-Fetch-Site": "cross-site"})

    assert resp.status_code == 200, resp.text


def test_csp_blocks_plugins_base_urls_and_foreign_forms() -> None:
    """A2 L1: the policy also sets object-src, base-uri and form-action."""
    directives = {d.strip() for d in CSP.split(";")}

    assert {"object-src 'none'", "base-uri 'none'", "form-action 'self'"} <= directives


@pytest.mark.parametrize(
    ("path", "host", "api"),
    [
        ("/api/health", HOST, True),
        ("/api/runs/nope", HOST, True),
        ("/api/health", "evil.example", True),
        ("/", HOST, False),
        ("/r/some~run/review", HOST, False),
    ],
)
def test_responses_carry_security_headers(
    guarded: TestClient, path: str, host: str, api: bool
) -> None:
    """§4.1: CSP, nosniff and no-referrer on every response; `/api` is also no-store."""
    resp = guarded.get(path, headers={"Host": host})

    assert resp.headers["content-security-policy"] == CSP
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    if api:
        assert "no-store" in resp.headers["cache-control"]


# --- refs, sources and static files ---------------------------------------------------------------


HOSTILE_REFS = [
    "/api/runs/..",
    "/api/runs/%2e%2e",
    "/api/runs/%2E%2E%2Foutside",
    "/api/runs/..~outside",
    "/api/runs/a~..~b",
    "/api/runs/runs~..~..~outside",
    "/api/runs/.~outside",
    "/api/runs/..%5Coutside",
    "/api/runs/%2Fetc%2Fpasswd",
    "/api/runs/{decoy}",
    "/api/runs/..~outside/matrix",
    "/api/runs/..~outside/proposals",
    "/api/decisions?run=..~outside",
    "/api/decisions?run=%2E%2E%2Foutside",
    "/api/export?run={decoy}&format=json&include_stale=0",
    "/api/diff?a=..~outside&b=..~outside",
]


@pytest.mark.parametrize("template", HOSTILE_REFS)
def test_hostile_refs_are_404(guarded: TestClient, workdir: Path, template: str) -> None:
    """§4.1, §4.2: refs resolve only through the index; `..`, encodings, absolute paths are 404."""
    raw = template.format(decoy=quote(str(workdir / "outside"), safe="")).encode()

    resp = raw_request(guarded, "GET", raw)

    assert resp.status == 404, (resp.status, resp.body[:200])
    assert b"DECOY-RUN" not in resp.body


@pytest.mark.parametrize(
    ("raw", "must_404"),
    [
        (b"/assets/../app.py", True),
        (b"/assets/%2e%2e/app.py", True),
        (b"/assets/..%2fapp.py", True),
        (b"/assets/%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fpyproject.toml", True),
        (b"/assets/../../../../pyproject.toml", True),
        (b"/assets//etc/passwd", True),
        (b"/assets/%2Fetc%2Fpasswd", True),
        (b"/assets/..%5c..%5capp.py", True),
        (b"/../../pyproject.toml", False),
        (b"/%2e%2e/%2e%2e/%2e%2e/%2e%2e/pyproject.toml", False),
        (b"/build.json/../../app.py", False),
    ],
)
def test_static_traversal_never_leaks_files(
    guarded: TestClient, raw: bytes, must_404: bool
) -> None:
    """§4.1, §6: static paths cannot leave the static dir; `/assets` misses are 404."""
    resp = raw_request(guarded, "GET", raw)

    if must_404:
        assert resp.status == 404, (resp.status, resp.body[:200])
    for marker in (b"def create_app", b"[project]", b"root:"):
        assert marker not in resp.body


@pytest.mark.parametrize(
    ("body", "may_succeed"),
    [
        pytest.param({"source_id": 7, "tier": "t1"}, False, id="unknown-id"),
        pytest.param({"source_id": "/etc/passwd", "tier": "t1"}, False, id="path-as-id"),
        pytest.param({"source": "/etc/passwd", "tier": "t1"}, False, id="path-field"),
        pytest.param({"source_id": 0, "resume": "..~outside"}, False, id="hostile-resume"),
        pytest.param(
            {"source_id": 0, "tier": "t1", "argv": ["--out", "/etc/passwd"], "cli": ["/bin/sh"]},
            True,
            id="argv-and-cli-fields",
        ),
    ],
)
def test_the_browser_never_chooses_a_path_or_an_argv(
    guarded: TestClient, fake_log: Path, body: dict[str, Any], may_succeed: bool
) -> None:
    """§4.1, §4.4: sources by allowlist id, refs by index; sent paths or argv never get spawned."""
    resp = guarded.post("/api/estimates", json=body, headers={TOKEN_HEADER: TOKEN})

    if may_succeed:
        assert resp.status_code in (201, 400, 422), resp.text
    else:
        assert 400 <= resp.status_code < 500, resp.text
    words = [word for record in invocations(fake_log) for word in record["argv"]]
    assert not any(bad in word for word in words for bad in ("/etc/passwd", "outside", "/bin/sh"))


# --- secrets --------------------------------------------------------------------------------------


def test_no_secret_in_any_response_header_or_log(
    workdir: Path,
    fake_log: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """§4.1, §6: with a canary key set, `sk-or-` appears in no body, header or log line."""
    monkeypatch.setenv("OPENROUTER_API_KEY", CANARY)
    caplog.set_level(logging.DEBUG)
    ui = ui_fixture_or_none()
    runs = ui.runs_root if ui else workdir / "runs"
    sources = (ui.source,) if ui else (workdir / "stories.md",)
    requests: list[tuple[str, str, Any]] = [
        ("GET", "/", None),
        ("GET", "/api/health", None),
        ("GET", "/api/runs?include=dry", None),
        ("GET", "/api/key", None),
        ("GET", "/api/runs/nope", None),
        ("POST", UNDO, {"run": "nope"}),
    ]
    if ui is not None:
        ref, pair = ui.latest, ui.run(ui.latest)["pair"]
        requests += [
            ("GET", f"/api/runs/{ref}", None),
            ("GET", f"/api/runs/{ref}/matrix", None),
            ("GET", f"/api/runs/{ref}/proposals", None),
            ("GET", f"/api/runs/{ref}/pairs/{pair['a']}/{pair['b']}", None),
            ("GET", f"/api/diff?a={ui.previous}&b={ref}", None),
            ("GET", f"/api/decisions?run={ref}", None),
            ("GET", f"/api/export?run={ref}&format=md&include_stale=1", None),
            ("GET", f"/api/export?run={ref}&format=json&include_stale=1", None),
        ]
    state = workdir / "state"
    state.mkdir(exist_ok=True)
    if ui is not None:
        shutil.copyfile(ui.seed, state / "decisions.json")

    with make_client(make_config(runs, state, sources)) as client:
        for method, url, body in requests:
            resp = client.request(method, url, json=body)
            where = f"{method} {url}"
            assert SECRET not in resp.text, where
            assert all(SECRET not in value for value in resp.headers.values()), where
        guarded = client.post(UNDO, json={"run": "nope"}, headers={TOKEN_HEADER: "wrong"})
        assert SECRET not in guarded.text

    assert all(SECRET not in record.getMessage() for record in caplog.records)
    out, err = capfd.readouterr()
    assert SECRET not in out + err


def test_failed_job_redacts_secret_from_tail_message_and_job_file(
    workdir: Path,
    fake_log: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """§4.1, §4.4: a CLI error line echoing the key reaches no job view, job.json or log."""
    monkeypatch.setenv("OPENROUTER_API_KEY", CANARY)
    monkeypatch.setenv("FAKE_CLI_MODE", "auth")  # its run error line echoes the key
    caplog.set_level(logging.DEBUG)
    cfg = make_config(workdir / "runs", workdir / "state", (workdir / "stories.md",))

    with make_client(cfg) as client:
        estimate = client.post("/api/estimates", json={"source_id": 0, "tier": "t1"})
        assert estimate.status_code == 201, estimate.text
        started = client.post(
            "/api/jobs",
            json={"estimate_id": estimate.json()["id"], "max_usd": 0.1, "confirm": "spend"},
        )
        assert started.status_code == 202, started.text
        job_id = started.json()["id"]
        job = wait_for(
            lambda: client.get(f"/api/jobs/{job_id}").json(), lambda j: j["state"] != "running"
        )
        listing = client.get("/api/jobs")

    assert (job["state"], job["exit_code"], job["message"]) == ("failed", 5, EXIT_MESSAGES[5])
    assert any("Jev refused the request" in line for line in job["log_tail"])
    assert SECRET not in json.dumps(job)
    assert SECRET not in listing.text
    job_files = list((workdir / "state").rglob("job.json"))
    assert job_files, "no job.json under the state dir"
    assert all(SECRET not in path.read_text("utf-8") for path in job_files)
    assert all(SECRET not in record.getMessage() for record in caplog.records)
    out, err = capfd.readouterr()
    assert SECRET not in out + err


def test_static_assets_contain_no_secret() -> None:
    """§6: no committed asset carries an `sk-or-` string."""
    files = [p for p in STATIC_DIR.rglob("*") if p.is_file()]

    assert files
    assert [p.name for p in files if SECRET.encode() in p.read_bytes()] == []


# --- import boundary ------------------------------------------------------------------------------

LOADED = (
    "import json, sys\n"
    f"print(json.dumps(sorted(m for m in {FORBIDDEN_MODULES!r} if m in sys.modules)))"
)

SERVE_REQUESTS = textwrap.dedent(
    """
    import asyncio, json, sys
    from pathlib import Path
    from tesselint.server import ServeConfig, create_app

    runs, state, paths = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
    cfg = ServeConfig(host="127.0.0.1", port=8765, runs_root=runs, state_dir=state, sources=(),
                      cli=("false",), ui_max_usd=1.0, token="t" * 43)
    app = create_app(cfg)

    async def main():
        state_dict = {}
        inbox = asyncio.Queue()
        started = asyncio.Event()
        await inbox.put({"type": "lifespan.startup"})

        async def lifespan_send(message):
            if message["type"].startswith("lifespan.startup"):
                started.set()

        lifespan = asyncio.create_task(app(
            {"type": "lifespan", "asgi": {"version": "3.0"}, "state": state_dict},
            inbox.get, lifespan_send))
        await started.wait()
        statuses = []
        for url in paths:
            sent = []
            path, _, query = url.partition("?")
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
                     "query_string": query.encode(), "root_path": "",
                     "client": ("127.0.0.1", 50000),
                     "headers": [(b"host", b"127.0.0.1:8765")], "server": ("127.0.0.1", 8765),
                     "state": dict(state_dict)}

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            await app(scope, receive, send)
            statuses.append(next(m["status"] for m in sent if m["type"] == "http.response.start"))
        await inbox.put({"type": "lifespan.shutdown"})
        await lifespan
        return statuses

    statuses = asyncio.run(main())
    forbidden = ("tesselint.jev", "httpx", "dotenv")
    print(json.dumps({"statuses": statuses,
                      "loaded": sorted(m for m in forbidden if m in sys.modules)}))
    """
)


def run_python(code: str, *args: str) -> str:
    """Run `code` in a fresh interpreter at the repo root; return its stdout's last line."""
    proc = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


def test_importing_server_loads_no_jev_httpx_or_dotenv() -> None:
    """§1 Boundary: `import tesselint.server` in a fresh process loads none of them."""
    loaded = json.loads(run_python("import tesselint.server\n" + LOADED))

    assert loaded == []


def test_serving_every_read_route_loads_no_jev_httpx_or_dotenv(tmp_path: Path) -> None:
    """§1 Boundary: deriving and serving runs (no TestClient: it imports httpx) loads none."""
    ui = ui_fixture_or_none()
    paths = ["/api/health", "/api/runs", "/"]
    runs = tmp_path / "runs"
    runs.mkdir()
    if ui is not None:
        runs = ui.runs_root
        ref, pair = ui.latest, ui.run(ui.latest)["pair"]
        paths += [
            f"/api/runs/{ref}",
            f"/api/runs/{ref}/matrix",
            f"/api/runs/{ref}/proposals",
            f"/api/runs/{ref}/pairs/{pair['a']}/{pair['b']}",
            f"/api/diff?a={ui.previous}&b={ref}",
            f"/api/decisions?run={ref}",
            f"/api/export?run={ref}&format=json&include_stale=1",
        ]
    state = str(tmp_path / "state")

    result = json.loads(run_python(SERVE_REQUESTS, str(runs), state, json.dumps(paths)))

    assert result["loaded"] == []
    assert result["statuses"] == [200] * len(paths)
