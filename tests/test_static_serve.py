"""Built assets and the `tesselint serve` command (docs/ui/DESIGN.md §1, §4.1, §5 Build, §6).

The build stamp must match `web/src/**` (minus `*.test.ts(x)`), `web/index.html`, the lockfile
and `web/vite.config.ts`
(stale committed assets fail here); the served index carries the token; `serve` binds loopback
only, never loads `.env` and relays key status from `TESSELINT_UI_CLI`.

`serve`, `serve_argv` and `serve_env` start real `tesselint serve` processes; test_decisions
imports them for the lock tests.
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from tesselint.server.app import STATIC_DIR
from tesselint.server.decisions import LOCK_FILE
from tesselint.server.guard import TOKEN_HEADER, TOKEN_PLACEHOLDER
from tests.fake_cli import FAKE_CLI, kill_leftovers, wait_for
from tests.test_server_api import (
    CANARY,
    FAKE_KEY_STATUS,
    MD_SOURCE,
    REPO,
    TOKEN,
    make_client,
    make_config,
)

WEB = REPO / "web"
SECRET = "sk-or-"
TOKEN_META = re.compile(r'<meta name="tesselint-token" content="([^"]+)"')


# --- build stamp and static files -----------------------------------------------------------------


def web_source_hash() -> str:
    """The web/scripts/stamp.mjs hash: repo-relative POSIX paths, sorted, `path\\0bytes\\0`."""
    files = [WEB / "index.html", WEB / "package-lock.json", WEB / "vite.config.ts"]
    for folder, _, names in os.walk(WEB / "src", followlinks=False):
        for name in names:
            path = Path(folder) / name
            is_test = name.endswith((".test.ts", ".test.tsx"))  # PLAN resolution 1
            if path.is_file() and not path.is_symlink() and not is_test:
                files.append(path)
    digest = hashlib.sha256()
    for rel, path in sorted((p.relative_to(REPO).as_posix(), p) for p in files):
        digest.update(rel.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def static_index() -> str:
    return (STATIC_DIR / "index.html").read_text("utf-8")


def test_build_stamp_matches_web_sources() -> None:
    """§5 Build: build.json's source_hash is the current sources' hash; else rebuild the assets."""
    stamp = json.loads((STATIC_DIR / "build.json").read_text("utf-8"))

    assert stamp == {"source_hash": web_source_hash()}, (
        "src/tesselint/server/static is stale: run `npm --prefix web run build`"
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    (tmp_path / "runs").mkdir()
    with make_client(make_config(tmp_path / "runs", tmp_path / "state")) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/runs",
        "/runs/diff?a=mini~20260101-090000&b=mini~20260102-090000",
        "/r/mini~20260102-090000/review?case=mg%3AUS-1.2&opt=2",
        "/r/mini~20260102-090000/story/US-1.1",
        "/runs/job/0123abcd",
    ],
)
def test_ui_routes_serve_the_index_with_the_token_injected(client: TestClient, path: str) -> None:
    """§4.1, §5: outside /api and /assets the server returns index.html with the token in place."""
    resp = client.get(path)

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    assert TOKEN_PLACEHOLDER not in resp.text
    assert resp.text == static_index().replace(TOKEN_PLACEHOLDER, TOKEN)


@pytest.mark.parametrize(
    "path", ["/api/nope", "/api/runs/x/no-such-view", "/assets/missing-0000.js"]
)
def test_api_and_asset_misses_are_404_not_the_index(client: TestClient, path: str) -> None:
    """§5 Routing: unknown /api and /assets paths are 404, never the token-bearing index."""
    resp = client.get(path)

    assert resp.status_code == 404
    assert TOKEN not in resp.text
    assert 'id="root"' not in resp.text


def test_built_assets_exist_and_are_served_byte_for_byte(client: TestClient) -> None:
    """§5 Build: every asset the index references is committed and served as JS or CSS."""
    refs = re.findall(r'(?:src|href)="(/assets/[^"]+)"', static_index())
    assert refs, "static/index.html references no /assets file"

    for ref in refs:
        resp = client.get(ref)

        assert resp.status_code == 200, ref
        assert resp.content == (STATIC_DIR / ref.lstrip("/")).read_bytes()
        assert any(kind in resp.headers["content-type"] for kind in ("javascript", "css")), ref


# --- the serve process ----------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def serve_env(extra: Mapping[str, str]) -> dict[str, str]:
    """The process env plus a canary key, an unroutable base URL and the fake CLI."""
    env = dict(os.environ)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "OPENROUTER_API_KEY": CANARY,
            "OPENROUTER_BASE_URL": "https://jev.invalid/api",
            "TESSELINT_UI_CLI": str(FAKE_CLI),
            "TESSELINT_SOURCE": str(MD_SOURCE),
        }
    )
    env.update(extra)
    return env


def serve_argv(workdir: Path, *extra: str, port: int | None = None) -> list[str]:
    """`python -m tesselint serve` on workdir/runs and workdir/state, never opening a browser."""
    runs = workdir / "runs"
    runs.mkdir(exist_ok=True)
    return [
        sys.executable,
        "-m",
        "tesselint",
        "serve",
        "--runs",
        str(runs),
        "--state",
        str(workdir / "state"),
        "--port",
        str(port if port is not None else free_port()),
        "--no-open",
        *extra,
    ]


@dataclass(frozen=True)
class Served:
    proc: subprocess.Popen[bytes]
    base: str
    state: Path
    output: Path

    def log(self) -> str:
        return self.output.read_text("utf-8", errors="replace")


def http(
    served: Served,
    method: str,
    path: str,
    body: Any = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, bytes]:
    """One request with urllib (Host is 127.0.0.1:port); JSON-encodes `body`."""
    data = None if body is None else json.dumps(body).encode()
    all_headers = {"Content-Type": "application/json"} if data is not None else {}
    all_headers.update(headers or {})
    request = urllib.request.Request(served.base + path, data, all_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return int(resp.status), bytes(resp.read())
    except urllib.error.HTTPError as err:
        return int(err.code), bytes(err.read())


@contextmanager
def serve(workdir: Path, *extra: str, env: Mapping[str, str] | None = None) -> Iterator[Served]:
    """Start `tesselint serve` in `workdir`, wait for /api/health, stop it on exit."""
    port = free_port()
    output = workdir / "serve.log"
    with output.open("wb") as out:
        proc = subprocess.Popen(
            serve_argv(workdir, *extra, port=port),
            cwd=workdir,
            env=serve_env(env or {}),
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    served = Served(proc, f"http://127.0.0.1:{port}", workdir / "state", output)
    try:
        deadline = time.monotonic() + 60
        while True:
            if proc.poll() is not None:
                pytest.fail(f"serve exited with {proc.returncode}:\n{served.log()}")
            try:
                if http(served, "GET", "/api/health")[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"serve did not answer /api/health:\n{served.log()}")
            time.sleep(0.1)
        yield served
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.10", "10.0.0.1", "busy port"])
def test_serve_exits_2_on_a_non_loopback_host_or_a_busy_port(tmp_path: Path, host: str) -> None:
    """§4.1: a non-loopback --host or a busy --port exits 2 with one error line."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        if host == "busy port":
            argv = serve_argv(tmp_path, port=busy.getsockname()[1])
        else:
            argv = serve_argv(tmp_path, "--host", host)

        proc = subprocess.run(
            argv,
            cwd=tmp_path,
            env=serve_env({}),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    assert proc.returncode == 2, proc.stderr
    assert proc.stderr.count("tesselint: error:") == 1, proc.stderr


DOTENV_SPY = textwrap.dedent(
    """
    import json, sys
    import tesselint.cli as cli

    calls = []
    cli.load_dotenv = lambda *args, **kwargs: calls.append("load_dotenv") or False
    cli.find_dotenv = lambda *args, **kwargs: calls.append("find_dotenv") or ""
    try:
        code = cli.main(sys.argv[1:])
    except SystemExit as exc:
        code = exc.code
    print(json.dumps({"code": code, "calls": calls}))
    """
)


@pytest.mark.parametrize(
    ("words", "loads_dotenv"),
    [
        pytest.param(["serve", "--host", "0.0.0.0"], False, id="serve"),
        pytest.param(["--debug", "serve", "--host", "0.0.0.0"], False, id="debug-serve"),
        pytest.param(["parse", "--source", str(MD_SOURCE), "--json"], True, id="parse-control"),
    ],
)
def test_serve_never_calls_load_dotenv(
    tmp_path: Path, words: list[str], loads_dotenv: bool
) -> None:
    """§1, §4.1: `main()` skips load_dotenv for serve (the parse control proves the spy works)."""
    argv = words
    if "serve" in words:
        argv = [*words, "--runs", str(tmp_path), "--state", str(tmp_path / "state"), "--no-open"]

    proc = subprocess.run(
        [sys.executable, "-c", DOTENV_SPY, *argv],
        cwd=tmp_path,
        env=serve_env({}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert bool(result["calls"]) is loads_dotenv, result


@pytest.fixture(scope="module")
def served_ui(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    """One real `serve`: canary key, fake CLI in `auth` mode, a planted `.env` it must ignore."""
    workdir = tmp_path_factory.mktemp("serve")
    (workdir / ".env").write_text("FAKE_CLI_REMAINING=0.01\n", "utf-8")
    source = workdir / "stories.md"
    shutil.copyfile(MD_SOURCE, source)
    log = workdir / "fake_cli.log"
    env = {"FAKE_CLI_MODE": "auth", "FAKE_CLI_LOG": str(log), "TESSELINT_SOURCE": str(source)}
    try:
        with serve(workdir, "--source", str(source), env=env) as served:
            yield served
    finally:
        kill_leftovers(log)


def page_token(served: Served) -> str:
    """The token the served index.html carries."""
    status, index = http(served, "GET", "/")
    assert status == 200
    match = TOKEN_META.search(index.decode("utf-8"))
    assert match is not None, "no tesselint-token meta tag"
    return match.group(1)


def test_serve_process_serves_the_ui_api_key_status_and_takes_the_lock(served_ui: Served) -> None:
    """§3 Locking, §4.1, §4.4: token injected, stamp in health, key via the UI CLI, no .env."""
    index = http(served_ui, "GET", "/")[1].decode("utf-8")
    token = page_token(served_ui)
    health_status, health = http(served_ui, "GET", "/api/health")
    key_status, key = http(served_ui, "GET", "/api/key")

    assert TOKEN_PLACEHOLDER not in index
    assert len(token) >= 32
    assert health_status == 200
    stamp = json.loads((STATIC_DIR / "build.json").read_text("utf-8"))
    assert json.loads(health)["build"] == stamp["source_hash"]
    assert key_status == 200
    assert json.loads(key) == FAKE_KEY_STATUS  # remaining 1.12, not the planted .env's 0.01
    undo = {"run": "nope"}
    assert http(served_ui, "POST", "/api/decisions/undo", undo)[0] == 403
    assert http(served_ui, "POST", "/api/decisions/undo", undo, {TOKEN_HEADER: token})[0] == 404
    assert (served_ui.state / LOCK_FILE).read_text("utf-8").strip() == str(served_ui.proc.pid)


def test_serve_process_output_never_contains_the_key(served_ui: Served) -> None:
    """§4.1, §6: a failing job whose CLI echoes the key leaves no `sk-or-` in responses or logs."""
    token = {TOKEN_HEADER: page_token(served_ui)}

    est_status, estimate = http(
        served_ui, "POST", "/api/estimates", {"source_id": 0, "tier": "t1"}, token
    )
    assert est_status == 201, estimate
    body = {"estimate_id": json.loads(estimate)["id"], "max_usd": 0.1, "confirm": "spend"}
    job_status, job = http(served_ui, "POST", "/api/jobs", body, token)
    assert job_status == 202, job
    job_id = json.loads(job)["id"]
    final = wait_for(
        lambda: http(served_ui, "GET", f"/api/jobs/{job_id}")[1],
        lambda raw: json.loads(raw)["state"] != "running",
    )
    time.sleep(0.5)

    assert json.loads(final)["exit_code"] == 5
    assert SECRET.encode() not in final
    assert SECRET not in served_ui.log()
