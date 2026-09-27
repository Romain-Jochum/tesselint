"""API error codes and their HTTP statuses (docs/ui/DESIGN.md §3, §4.1, §7.2)."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class ErrorCode(StrEnum):
    """`error.code` values; the body is `{"error": {"code", "message"}}` (§7.2)."""

    BAD_HOST = "bad_host"
    CSRF = "csrf"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"  # §4.1 names only the status, 415
    BAD_REQUEST = "bad_request"  # additive: a malformed body or parameter
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"  # additive
    PAYLOAD_TOO_LARGE = "payload_too_large"  # additive: a POST body over the route limit
    RUN_NOT_FOUND = "run_not_found"
    RUN_UNREADABLE = "run_unreadable"  # additive: an indexed run whose files cannot be derived
    JOB_RUNNING = "job_running"
    ESTIMATE_STALE = "estimate_stale"
    NOTHING_TO_UNDO = "nothing_to_undo"
    BAD_THRESHOLD = "bad_threshold"
    UNKNOWN_KEY = "unknown_key"
    CAP_INVALID = "cap_invalid"
    DECISIONS_UNREADABLE = "decisions_unreadable"
    INTERNAL = "internal"  # a 500 carries no details (§4.1)
    CLI_FAILED = "cli_failed"


HTTP_STATUS: Mapping[ErrorCode, int] = MappingProxyType(
    {
        ErrorCode.BAD_HOST: 403,
        ErrorCode.CSRF: 403,
        ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
        ErrorCode.BAD_REQUEST: 400,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.METHOD_NOT_ALLOWED: 405,
        ErrorCode.PAYLOAD_TOO_LARGE: 413,
        ErrorCode.RUN_NOT_FOUND: 404,
        ErrorCode.RUN_UNREADABLE: 422,
        ErrorCode.JOB_RUNNING: 409,
        ErrorCode.ESTIMATE_STALE: 409,
        ErrorCode.NOTHING_TO_UNDO: 409,
        ErrorCode.BAD_THRESHOLD: 422,
        ErrorCode.UNKNOWN_KEY: 422,
        ErrorCode.CAP_INVALID: 422,
        ErrorCode.DECISIONS_UNREADABLE: 500,
        ErrorCode.INTERNAL: 500,
        ErrorCode.CLI_FAILED: 502,
    }
)


class ApiError(Exception):
    """An error the API returns as `{"error": {"code", "message"}}` with HTTP_STATUS[code]."""

    code: ErrorCode
    message: str

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
