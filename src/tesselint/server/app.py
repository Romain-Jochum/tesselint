"""The `tesselint serve` application and its settings (docs/ui/DESIGN.md §4.1, §7.1, §7.2).

`create_app` builds the Starlette app: the §7.2 routes, the built UI with the token injected, the
SPA fallback and LocalGuard. `run_server` is `tesselint serve`: loopback bind, the state lock and
uvicorn with redacted logs. Nothing here imports `tesselint.jev`, `httpx` or `dotenv` (§1).
"""

import json
import logging
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from types import FrameType
from typing import Any, cast

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from starlette.routing import Route

import tesselint
from tesselint.errors import UsageError
from tesselint.models import Thresholds
from tesselint.parse import is_v1
from tesselint.protocol import KeyStatus
from tesselint.server.decisions import DECISIONS_FILE, LOCK_FILE, DecisionStore, pid_alive
from tesselint.server.diff import diff_runs
from tesselint.server.errors import ApiError, ErrorCode
from tesselint.server.export import (
    build_export,
    export_markdown_doc,
    write_export,
)
from tesselint.server.guard import (
    SECURITY_HEADERS,
    TOKEN_PLACEHOLDER,
    LocalGuard,
    error_response,
    is_api_path,
    redact,
)
from tesselint.server.jobs import JobManager, RunScope
from tesselint.server.payloads import (
    DecisionChange,
    DecisionsResponse,
    ExportWritten,
    Health,
    ProposalsResponse,
    RunsResponse,
    SourceInfo,
)
from tesselint.server.runindex import latest
from tesselint.server.runstore import RunStore, summarize

STATIC_DIR = Path(__file__).parent / "static"  # built by `npm --prefix web run build` (§5)
UI_CLI_ENV = "TESSELINT_UI_CLI"  # tests point JobManager at a fake CLI (§4.4)
DEFAULT_CLI = (sys.executable, "-m", "tesselint")  # PLAN resolution 5
BUILD_FILE = "build.json"
INDEX_FILE = "index.html"
ASSETS = "/assets"
KEY_TIMEOUT_S = 60.0  # `tesselint key --json` is one free GET
MAX_BODY_BYTES = 1_000_000  # per POST
MAX_CHANGES = 1000  # per decisions batch
MAX_NOTE_CHARS = 10_000
THRESHOLD_PARAMS = ("merge", "cluster", "split")  # the §7.2 `?merge&cluster&split` overrides
ACTIONS = ("accept", "reject", "defer", "clear")
FILE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")  # one static file, no path
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SCOPE_WORD = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$")  # a tier or epic; never a flag
ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
MEDIA_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
ASSET_CACHE = "public, max-age=31536000, immutable"  # hashed file names
LOOPBACK_BINDS: Mapping[str, tuple[socket.AddressFamily, str, str]] = {
    # --host → (family, bind address, URL host); the guard accepts exactly these Host names
    "127.0.0.1": (socket.AF_INET, "127.0.0.1", "127.0.0.1"),
    "localhost": (socket.AF_INET, "127.0.0.1", "localhost"),
    "::1": (socket.AF_INET6, "::1", "[::1]"),
    "[::1]": (socket.AF_INET6, "::1", "[::1]"),
}

LOG = logging.getLogger("tesselint.server")


@dataclass(frozen=True, slots=True)
class ServeConfig:
    """Everything `create_app` needs, resolved by `tesselint serve` (§4.1, §7.1)."""

    host: str
    port: int
    runs_root: Path
    state_dir: Path
    sources: tuple[Path, ...]  # the allowlist; the API names them by index (`source_id`)
    cli: tuple[str, ...]  # argv prefix of the CLI; default [sys.executable, "-m", "tesselint"]
    ui_max_usd: float
    token: str = field(repr=False)
    static_dir: Path = STATIC_DIR


def ui_cli() -> tuple[str, ...]:
    """The CLI argv prefix: `TESSELINT_UI_CLI` split with shlex, else `python -m tesselint`."""
    raw = os.environ.get(UI_CLI_ENV, "").strip()
    return tuple(shlex.split(raw)) if raw else DEFAULT_CLI


def create_app(cfg: ServeConfig) -> Starlette:
    """Build the Starlette app: §7.2 routes, static assets, SPA fallback, LocalGuard (§4.1)."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    api = _Api(cfg)

    def get(path: str, endpoint: Callable[[Request], Awaitable[Response]]) -> Route:
        return Route(path, endpoint, methods=["GET"])

    def post(path: str, endpoint: Callable[[Request], Awaitable[Response]]) -> Route:
        return Route(path, endpoint, methods=["POST"])

    routes = [
        get("/api/health", api.health),
        get("/api/runs", api.runs),
        get("/api/runs/{ref}", api.bundle),
        get("/api/runs/{ref}/matrix", api.matrix),
        get("/api/runs/{ref}/proposals", api.proposals),
        get("/api/runs/{ref}/pairs/{a}/{b}", api.pair),
        get("/api/diff", api.diff),
        get("/api/decisions", api.decisions),
        post("/api/decisions", api.post_decisions),
        post("/api/decisions/undo", api.undo),
        get("/api/export", api.export),
        post("/api/exports", api.write_exports),
        get("/api/key", api.key),
        post("/api/estimates", api.estimate),
        get("/api/jobs", api.active_job),
        post("/api/jobs", api.start_job),
        get("/api/jobs/{job_id}", api.job),
        post("/api/jobs/{job_id}/cancel", api.cancel_job),
        Route("/{path:path}", api.static, methods=ALL_METHODS),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(LocalGuard, port=cfg.port, token=cfg.token)],
        exception_handlers={HTTPException: _http_error, Exception: _server_error},
    )


# --- endpoints ------------------------------------------------------------------------------------

Endpoint = Callable[["_Api", Request], Awaitable[Response]]


def _endpoint(fn: Endpoint) -> Endpoint:
    """ApiError → its JSON error; anything unexpected → a logged, detail-free 500 (§4.1)."""

    @wraps(fn)
    async def wrapped(self: "_Api", request: Request) -> Response:
        try:
            return await fn(self, request)
        except ApiError as e:
            return error_response(e.code, e.message)
        except HTTPException:
            raise  # e.g. 413 from the body limit: _http_error renders it
        except Exception as e:
            _log_unexpected(request, e)
            return error_response(ErrorCode.INTERNAL, "internal error")

    return wrapped


class _Api:
    """The §7.2 routes over one RunStore, DecisionStore and JobManager."""

    def __init__(self, cfg: ServeConfig) -> None:
        self.cfg = cfg
        self.store = RunStore(cfg.runs_root, sources=cfg.sources)
        self._lock = threading.Lock()
        self._decisions: DecisionStore | None = None
        self._jobs: JobManager | None = None

    def decision_store(self) -> DecisionStore:
        with self._lock:
            if self._decisions is None:
                self._decisions = DecisionStore(self.cfg.state_dir / DECISIONS_FILE)
            return self._decisions

    def job_manager(self) -> JobManager:
        with self._lock:
            if self._jobs is None:
                cfg = self.cfg
                self._jobs = JobManager(cfg.state_dir, cfg.cli, cfg.runs_root, cfg.ui_max_usd)
            return self._jobs

    # GET

    @_endpoint
    async def health(self, request: Request) -> Response:
        def work() -> Health:
            sources = [
                SourceInfo(
                    id=i, path=str(p), format="json" if is_v1(p) else "md", exists=p.is_file()
                )
                for i, p in enumerate(self.store.sources())
            ]
            return Health(
                version=tesselint.__version__,
                build=_build_stamp(self.cfg.static_dir),
                sources=sources,
                ui_max_usd=self.cfg.ui_max_usd,
            )

        return JSONResponse(await run_in_threadpool(work))

    @_endpoint
    async def runs(self, request: Request) -> Response:
        include_dry = request.query_params.get("include") == "dry"

        def work() -> RunsResponse:
            every = self.store.entries(include_dry=True)
            best = latest(every)
            shown = [e for e in every if include_dry or not e.dry_run]
            return RunsResponse(
                latest=best.ref if best else None, runs=[summarize(e) for e in shown]
            )

        return JSONResponse(await run_in_threadpool(work))

    @_endpoint
    async def bundle(self, request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(self.store.bundle, _path(request, "ref")))

    @_endpoint
    async def matrix(self, request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(self.store.matrix, _path(request, "ref")))

    @_endpoint
    async def proposals(self, request: Request) -> Response:
        ref = _path(request, "ref")

        def work() -> ProposalsResponse:
            th = _with_overrides(self.store.thresholds(ref), request.query_params)
            return self.store.proposals(ref, th)

        return JSONResponse(await run_in_threadpool(work))

    @_endpoint
    async def pair(self, request: Request) -> Response:
        ref = _path(request, "ref")
        a, b = _path(request, "a"), _path(request, "b")
        return JSONResponse(await run_in_threadpool(self.store.pair, ref, a, b))

    @_endpoint
    async def diff(self, request: Request) -> Response:
        a = request.query_params.get("a", "")
        b = request.query_params.get("b", "")

        def work() -> Any:
            old, new = self.store.derived(a), self.store.derived(b)
            return diff_runs(old, new, self.decision_store().read())

        return JSONResponse(await run_in_threadpool(work))

    @_endpoint
    async def decisions(self, request: Request) -> Response:
        ref = request.query_params.get("run", "")

        def work() -> DecisionsResponse:
            self.store.entry(ref)
            th = _with_overrides(self.store.thresholds(ref), request.query_params)
            store = self.decision_store()
            rev = store.read()["rev"]
            return self._views(store, ref, th, rev)

        return JSONResponse(await run_in_threadpool(work))

    @_endpoint
    async def export(self, request: Request) -> Response:
        params = request.query_params
        ref = params.get("run", "")

        def work() -> Response:
            self.store.entry(ref)
            fmt = params.get("format", "json")
            if fmt not in ("json", "md"):
                raise ApiError(ErrorCode.BAD_REQUEST, "format must be json or md")
            doc = self._export_doc(ref, _flag(params.get("include_stale"), "include_stale"))
            disposition = {
                "Content-Disposition": f'attachment; filename="{_export_name(ref)}.{fmt}"'
            }
            if fmt == "json":
                return JSONResponse(doc, headers=disposition)
            markdown = export_markdown_doc(doc)
            return Response(
                markdown, media_type="text/markdown; charset=utf-8", headers=disposition
            )

        return await run_in_threadpool(work)

    @_endpoint
    async def key(self, request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(self._key_status))

    @_endpoint
    async def active_job(self, request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(lambda: self.job_manager().active()))

    @_endpoint
    async def job(self, request: Request) -> Response:
        job_id = _job_id(request)

        def work() -> Any:
            found = self.job_manager().get(job_id)
            if found is None:
                raise ApiError(ErrorCode.NOT_FOUND, f"no job {job_id}")
            return found

        return JSONResponse(await run_in_threadpool(work))

    # POST

    @_endpoint
    async def post_decisions(self, request: Request) -> Response:
        body = await _json_body(request)

        def work() -> DecisionsResponse:
            ref = _text(body.get("run"), "run")
            entry = self.store.entry(ref)
            th = _with_overrides(self.store.thresholds(ref), _object(body.get("thresholds")), True)
            changes = _changes(body.get("changes"))
            store = self.decision_store()
            snapshot = entry.meta.get("snapshot_sha256")
            derived = self.store.derived(ref, th)
            rev = store.apply(ref, derived, snapshot if isinstance(snapshot, str) else "", changes)
            return self._views(store, ref, th, rev)

        return JSONResponse(await run_in_threadpool(work))

    @_endpoint
    async def undo(self, request: Request) -> Response:
        body = await _json_body(request)

        def work() -> DecisionsResponse:
            ref = _text(body.get("run"), "run")
            self.store.entry(ref)
            th = _with_overrides(self.store.thresholds(ref), _object(body.get("thresholds")), True)
            store = self.decision_store()
            return self._views(store, ref, th, store.undo())

        return JSONResponse(await run_in_threadpool(work))

    @_endpoint
    async def write_exports(self, request: Request) -> Response:
        body = await _json_body(request)

        def work() -> ExportWritten:
            ref = _text(body.get("run"), "run")
            self.store.entry(ref)
            include_stale = body.get("include_stale", False)
            if not isinstance(include_stale, bool):
                raise ApiError(ErrorCode.BAD_REQUEST, "include_stale must be true or false")
            doc = self._export_doc(ref, include_stale)
            md_path, json_path = write_export(doc, self.cfg.state_dir)
            return ExportWritten(md_path=str(md_path), json_path=str(json_path))

        return JSONResponse(await run_in_threadpool(work), status_code=201)

    @_endpoint
    async def estimate(self, request: Request) -> Response:
        body = await _json_body(request)

        def work() -> Any:
            source_id = body.get("source_id")
            if isinstance(source_id, bool) or not isinstance(source_id, int):
                raise ApiError(
                    ErrorCode.BAD_REQUEST, "source_id must be the id of an allowlisted source"
                )
            source = self.store.source(source_id)
            if source is None:
                raise ApiError(ErrorCode.NOT_FOUND, f"no allowlisted source {source_id}")
            tier = _scope_word(body.get("tier"), "tier")
            epics = _epics(body.get("epics"))
            if tier is not None and epics is not None:
                raise ApiError(ErrorCode.BAD_REQUEST, "give a tier or epics, not both")
            resume = body.get("resume")
            if resume is not None:
                resume = self.store.entry(_text(resume, "resume")).ref
            scope = RunScope(tier=tier, epics=epics, resume=resume)
            return self.job_manager().estimate(source, scope)

        return JSONResponse(await run_in_threadpool(work), status_code=201)

    @_endpoint
    async def start_job(self, request: Request) -> Response:
        body = await _json_body(request)

        def work() -> Any:
            estimate_id = _text(body.get("estimate_id"), "estimate_id")
            max_usd = body.get("max_usd")
            if isinstance(max_usd, bool) or not isinstance(max_usd, int | float):
                raise ApiError(ErrorCode.BAD_REQUEST, "max_usd must be a number of US$")
            if not math.isfinite(max_usd):
                raise ApiError(ErrorCode.CAP_INVALID, "max_usd must be a finite number")
            if body.get("confirm") != "spend":
                raise ApiError(ErrorCode.BAD_REQUEST, 'confirm must be "spend" to allow spending')
            key = self._key_status()
            return self.job_manager().start(estimate_id, float(max_usd), key)

        return JSONResponse(await run_in_threadpool(work), status_code=202)

    @_endpoint
    async def cancel_job(self, request: Request) -> Response:
        job_id = _job_id(request)

        def work() -> Any:
            jobs = self.job_manager()
            if jobs.get(job_id) is None:
                raise ApiError(ErrorCode.NOT_FOUND, f"no job {job_id}")
            return jobs.cancel(job_id)

        return JSONResponse(await run_in_threadpool(work), status_code=202)

    # the UI

    @_endpoint
    async def static(self, request: Request) -> Response:
        """Built assets, then index.html with the token for every other path (§4.1, §5)."""
        path: str = request.scope["path"]
        if is_api_path(path):
            return error_response(ErrorCode.NOT_FOUND, "no such API route")
        if request.method not in ("GET", "HEAD"):
            return error_response(ErrorCode.METHOD_NOT_ALLOWED, "the UI is read with GET")
        if path == ASSETS or path.startswith(ASSETS + "/"):
            return _file(self.cfg.static_dir / "assets", path.removeprefix(ASSETS + "/"))
        name = path.removeprefix("/")
        if name != INDEX_FILE and FILE_NAME.fullmatch(name):
            found = _file(self.cfg.static_dir, name)
            if found.status_code == 200:
                return found
        return self._index()

    # helpers

    def _index(self) -> Response:
        try:
            page = (self.cfg.static_dir / INDEX_FILE).read_text("utf-8")
        except OSError:
            return PlainTextResponse(
                "tesselint: the web UI is not built (npm --prefix web run build)", status_code=503
            )
        page = page.replace(TOKEN_PLACEHOLDER, self.cfg.token)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    def _views(self, store: DecisionStore, ref: str, th: Thresholds, rev: int) -> DecisionsResponse:
        derived = self.store.derived(ref, th)
        views = store.views(derived, self.store.current_source(ref))
        return DecisionsResponse(rev=rev, decisions=views)

    def _export_doc(self, ref: str, include_stale: bool) -> dict[str, Any]:
        derived = self.store.derived(ref)
        views = self.decision_store().views(derived, self.store.current_source(ref))
        return build_export(
            views, self.store.bundle(ref), include_stale=include_stale, derived=derived
        )

    def _key_status(self) -> KeyStatus:
        """`tesselint key --json` in a subprocess: the only way serve learns about the key."""
        argv = [*self.cfg.cli, "key", "--json"]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=KEY_TIMEOUT_S,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ApiError(ErrorCode.CLI_FAILED, f"tesselint key --json failed: {e}") from e
        status = _key_line(proc.stdout)
        if status is None:
            lines = [line for line in proc.stderr.splitlines() if line.strip()]
            why = lines[-1] if lines else f"exit code {proc.returncode}"
            raise ApiError(ErrorCode.CLI_FAILED, f"tesselint key --json failed: {why}")
        return status


# --- request parsing ------------------------------------------------------------------------------


def _path(request: Request, name: str) -> str:
    return str(request.path_params[name])


def _job_id(request: Request) -> str:
    job_id = _path(request, "job_id")
    if not JOB_ID.fullmatch(job_id):
        raise ApiError(ErrorCode.NOT_FOUND, "not a job id")
    return job_id


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        body = json.loads(raw) if raw.strip() else {}
    except (ValueError, UnicodeDecodeError) as e:
        raise ApiError(ErrorCode.BAD_REQUEST, "the body is not valid JSON") from e
    if not isinstance(body, dict):
        raise ApiError(ErrorCode.BAD_REQUEST, "the body must be a JSON object")
    return body


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ApiError(ErrorCode.BAD_REQUEST, f"{name} must be a non-empty string")
    return value


def _object(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ApiError(ErrorCode.BAD_THRESHOLD, "thresholds must be an object")
    return value


def _flag(value: str | None, name: str) -> bool:
    if value is None or value in ("0", "false", ""):
        return False
    if value in ("1", "true"):
        return True
    raise ApiError(ErrorCode.BAD_REQUEST, f"{name} must be 1 or 0")


def _with_overrides(
    base: Thresholds, values: Mapping[str, Any], strict: bool = False
) -> Thresholds:
    """`base` with the merge, cluster and split overrides, each a number in [0, 1] (§7.2).

    Query strings also carry other parameters; a JSON `thresholds` object (`strict`) may not.
    """
    unknown = [name for name in values if name not in THRESHOLD_PARAMS]
    if strict and unknown:
        raise ApiError(ErrorCode.BAD_THRESHOLD, f"unknown threshold {unknown[0]!r}")
    changes: dict[str, Any] = {
        name: _probability(name, values[name]) for name in THRESHOLD_PARAMS if name in values
    }
    return replace(base, **changes)


def _probability(name: str, raw: Any) -> float:
    value: float | None = None
    if isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            value = None
    elif isinstance(raw, int | float) and not isinstance(raw, bool):
        value = float(raw)
    if value is None or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ApiError(ErrorCode.BAD_THRESHOLD, f"{name} must be a number from 0 to 1")
    return value


def _changes(raw: Any) -> list[DecisionChange]:
    """The browser's changes: keys, actions and notes only (§7.2); the store checks the keys."""
    if not isinstance(raw, list) or len(raw) > MAX_CHANGES:
        raise ApiError(ErrorCode.BAD_REQUEST, f"changes must be a list of at most {MAX_CHANGES}")
    changes: list[DecisionChange] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"]:
            raise ApiError(ErrorCode.BAD_REQUEST, "each change needs a proposal key")
        change = DecisionChange(key=item["key"])
        action, note = item.get("action"), item.get("note")
        if action is not None:
            if action not in ACTIONS:
                raise ApiError(ErrorCode.BAD_REQUEST, f"action must be one of {', '.join(ACTIONS)}")
            change["action"] = action
        if note is not None:
            if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS:
                raise ApiError(ErrorCode.BAD_REQUEST, f"a note is text of at most {MAX_NOTE_CHARS}")
            change["note"] = note
        changes.append(change)
    return changes


def _scope_word(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not SCOPE_WORD.fullmatch(value):
        raise ApiError(ErrorCode.BAD_REQUEST, f"{name} must be a short name such as t1 or E3")
    return value


def _epics(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ApiError(ErrorCode.BAD_REQUEST, "epics must be a non-empty list of epic IDs")
    return tuple(cast(str, _scope_word(e, "each epic")) for e in value)


def _key_line(stdout: str) -> KeyStatus | None:
    """The KeyStatus in `key --json` output (its last JSON line), checked field by field."""
    for line in reversed(stdout.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("present"), bool):
            return None
        numbers = [data.get(k) for k in ("usage_usd", "limit_usd", "remaining_usd")]
        if any(
            n is not None and (isinstance(n, bool) or not isinstance(n, int | float))
            for n in numbers
        ):
            return None
        error = data.get("error")
        return KeyStatus(
            present=data["present"],
            usage_usd=None if numbers[0] is None else float(numbers[0]),
            limit_usd=None if numbers[1] is None else float(numbers[1]),
            remaining_usd=None if numbers[2] is None else float(numbers[2]),
            error=error if isinstance(error, str) else None,
        )
    return None


# --- files ----------------------------------------------------------------------------------------


def _build_stamp(static_dir: Path) -> str:
    try:
        stamp = json.loads((static_dir / BUILD_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return ""
    value = stamp.get("source_hash") if isinstance(stamp, dict) else None
    return value if isinstance(value, str) else ""


def _file(folder: Path, name: str) -> Response:
    """One regular, non-symlinked file named `name` directly in `folder`, else 404 (§4.1)."""
    path = folder / name
    if not FILE_NAME.fullmatch(name) or path.is_symlink() or not path.is_file():
        return error_response(ErrorCode.NOT_FOUND, "no such file")
    media_type = MEDIA_TYPES.get(path.suffix)
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": ASSET_CACHE})


def _export_name(ref: str) -> str:
    return f"decisions-{ref}"


# --- errors and logs ------------------------------------------------------------------------------


async def _http_error(request: Request, exc: Exception) -> Response:
    """Starlette's 404, 405 and 413 as `{"error": {"code", "message"}}`."""
    assert isinstance(exc, HTTPException)
    codes = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.METHOD_NOT_ALLOWED}
    codes[413] = ErrorCode.PAYLOAD_TOO_LARGE
    code = codes.get(exc.status_code, ErrorCode.BAD_REQUEST)
    body = {"error": {"code": code.value, "message": redact(str(exc.detail))}}
    return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)


async def _server_error(request: Request, exc: Exception) -> Response:
    """A 500 outside an endpoint: logged, redacted, no details (§4.1)."""
    _log_unexpected(request, exc)
    response = error_response(ErrorCode.INTERNAL, "internal error")
    for name, value in SECURITY_HEADERS:  # this response bypasses LocalGuard's headers
        response.headers[name] = value
    return response


def _log_unexpected(request: Request, exc: BaseException) -> None:
    detail = "".join(traceback.format_exception(exc))
    LOG.error("%s", redact(f"{request.method} {request.url.path} failed:\n{detail}"))


class RedactFilter(logging.Filter):
    """Removes OpenRouter keys from every log record, its arguments and its traceback (§4.1)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return True


LOG_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": RedactFilter}},
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "filters": ["redact"],
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "filters": ["redact"],
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        "tesselint": {"handlers": ["default"], "level": "INFO", "propagate": False},
    },
}


# --- tesselint serve ------------------------------------------------------------------------------


def run_server(cfg: ServeConfig, *, open_browser: bool) -> None:
    """`tesselint serve`: bind loopback, take the state lock, serve until Ctrl-C (§3, §4.1).

    A non-loopback host, a busy port or a lock held by a live serve raise UsageError (exit 2)
    before anything is served. SIGTERM stops the server like Ctrl-C and releases the lock.
    """
    if cfg.host not in LOOPBACK_BINDS:
        raise UsageError(f"--host {cfg.host} is not a loopback address (use 127.0.0.1 or ::1)")
    family, address, url_host = LOOPBACK_BINDS[cfg.host]
    sock = _bind(family, address, cfg.port)
    try:
        release = take_lock(cfg.state_dir)
        try:
            _serve(cfg, sock, f"http://{url_host}:{cfg.port}/", open_browser)
        finally:
            release()
    finally:
        sock.close()


def _bind(family: socket.AddressFamily, address: str, port: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((address, port))
    except OSError as e:
        sock.close()
        raise UsageError(f"cannot listen on {address} port {port}: {e.strerror or e}") from None
    return sock


def _serve(cfg: ServeConfig, sock: socket.socket, url: str, open_browser: bool) -> None:
    import uvicorn  # only `serve` needs it

    app = create_app(cfg)
    config = uvicorn.Config(
        app,
        host=cfg.host,
        port=cfg.port,
        log_config=LOG_CONFIG,
        proxy_headers=False,  # nothing sits in front of a loopback server
        server_header=False,
        lifespan="on",
        env_file=None,
    )
    server = uvicorn.Server(config)
    print(f"tesselint serve: {url} (Ctrl-C to stop)", flush=True)
    if open_browser:
        threading.Thread(target=_open_when_up, args=(server, url), daemon=True).start()
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        server.run(sockets=[sock])
    finally:
        signal.signal(signal.SIGTERM, previous)


def _interrupt(signum: int, frame: FrameType | None) -> None:
    """SIGTERM, re-raised by uvicorn after its graceful shutdown, ends serve like Ctrl-C."""
    raise KeyboardInterrupt


def _open_when_up(server: Any, url: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not server.started:
        if server.should_exit or time.monotonic() > deadline:
            return
        time.sleep(0.05)
    webbrowser.open(url)


def take_lock(state_dir: Path) -> Callable[[], None]:
    """Write our pid to `<state>/.lock` (O_EXCL); a live holder is a UsageError (§3 Locking).

    A lock whose pid is dead (or unreadable) is taken over. Returns the release function.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / LOCK_FILE
    me = os.getpid()
    for _ in range(3):
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            holder = _lock_pid(lock)
            if holder is not None and holder != me and pid_alive(holder):
                raise UsageError(
                    f"another tesselint serve (pid {holder}) uses {state_dir}; "
                    "stop it or pass another --state"
                ) from None
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{me}\n")
        return lambda: _release(lock, me)
    raise UsageError(f"cannot take the lock {lock}")


def _lock_pid(lock: Path) -> int | None:
    try:
        return int(lock.read_text("utf-8").strip())
    except (OSError, ValueError):
        return None


def _release(lock: Path, pid: int) -> None:
    if _lock_pid(lock) == pid:
        lock.unlink(missing_ok=True)
