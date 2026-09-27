"""Loopback, CSRF and header guard for every request (docs/ui/DESIGN.md §4.1)."""

import hmac
import re

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tesselint.server.errors import HTTP_STATUS, ErrorCode

TOKEN_HEADER = "X-TesseLint-Token"
TOKEN_PLACEHOLDER = "__TESSELINT_TOKEN__"  # in static/index.html's tesselint-token meta tag
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")  # the Host header must be one of these:PORT
SAFE_FETCH_SITES = ("same-origin", "none")
CSP = (
    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)
API_CACHE_CONTROL = "no-store"  # on /api responses only
SECRET_PATTERN = re.compile(r"sk-or-[A-Za-z0-9_-]+")  # redacted from logs and errors
REDACTED = "[redacted key]"  # never contains the pattern's prefix
SAFE_METHODS = ("GET", "HEAD")
JSON_MEDIA_TYPE = "application/json"


def redact(text: str) -> str:
    """`text` with every OpenRouter key replaced by REDACTED (§4.1)."""
    return SECRET_PATTERN.sub(REDACTED, text)


def is_api_path(path: str) -> bool:
    """Whether `path` is under `/api` (JSON, `no-store`)."""
    return path == "/api" or path.startswith("/api/")


def error_response(code: ErrorCode, message: str) -> JSONResponse:
    """`{"error": {"code", "message"}}` with the code's status; the message is redacted (§7.2)."""
    body = {"error": {"code": code.value, "message": redact(message)}}
    return JSONResponse(body, status_code=HTTP_STATUS[code])


class LocalGuard:
    """Pure ASGI middleware: Host, token, Origin, Sec-Fetch-Site and JSON checks (§4.1).

    Safe methods need only the Host, plus a same-origin Sec-Fetch-Site under /api; any other
    method also needs the page's token, a same-origin Origin and a JSON body.
    """

    def __init__(self, app: ASGIApp, *, port: int, token: str) -> None:
        """Wrap `app`; `token` is the startup `secrets.token_urlsafe(32)` (§4.1)."""
        self.app = app
        self.token = token.encode("utf-8")
        self.hosts = frozenset(f"{host}:{port}" for host in LOOPBACK_HOSTS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """403 `bad_host` or `csrf`, 415 for a non-JSON body; add security headers (§4.1)."""
        if scope["type"] == "websocket":  # the UI has no socket; a rebinding page gets none
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        guarded = _with_headers(send, api=is_api_path(scope["path"]))
        refusal = self._refusal(scope)
        if refusal is not None:
            await error_response(*refusal)(scope, receive, guarded)
            return
        await self.app(scope, receive, guarded)

    def _refusal(self, scope: Scope) -> tuple[ErrorCode, str] | None:
        """Why the request is refused, or None: Host first, then CSRF, then the body type."""
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        host = hosts[0].lower() if len(hosts) == 1 else ""
        if host not in self.hosts:
            return ErrorCode.BAD_HOST, "the Host header must be this server's loopback address"
        if scope["method"] in SAFE_METHODS:
            # A foreign page may not read /api either: /api/key, for one, spawns the CLI.
            if is_api_path(scope["path"]) and not _fetch_site_ok(headers):
                return ErrorCode.CSRF, "cross-site requests are refused"
            return None
        if not self._token_ok(headers.getlist(TOKEN_HEADER)):
            return ErrorCode.CSRF, f"a {TOKEN_HEADER} header with the page's token is required"
        origins = headers.getlist("origin")
        if origins and (len(origins) > 1 or origins[0].lower() != f"http://{host}"):
            return ErrorCode.CSRF, "cross-origin requests are refused"
        if not _fetch_site_ok(headers):
            return ErrorCode.CSRF, "cross-site requests are refused"
        media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != JSON_MEDIA_TYPE:
            return ErrorCode.UNSUPPORTED_MEDIA_TYPE, "the body must be application/json"
        return None

    def _token_ok(self, values: list[str]) -> bool:
        if len(values) != 1 or not values[0]:
            return False
        return hmac.compare_digest(values[0].encode("utf-8"), self.token)


def _fetch_site_ok(headers: Headers) -> bool:
    """No Sec-Fetch-Site (an old browser, a script), or only same-origin or none (a typed URL)."""
    return all(site.lower() in SAFE_FETCH_SITES for site in headers.getlist("sec-fetch-site"))


def _with_headers(send: Send, *, api: bool) -> Send:
    """`send` that adds the §4.1 headers to the response start (and `no-store` under /api)."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            for name, value in SECURITY_HEADERS:
                headers[name] = value
            if api:
                headers["Cache-Control"] = API_CACHE_CONTROL
        await send(message)

    return wrapped
