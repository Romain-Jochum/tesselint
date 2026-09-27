"""`tesselint serve`: the local web UI's HTTP server (docs/ui/DESIGN.md §4, §7.1, §7.2).

Never loads `.env` and never imports `tesselint.jev`, `httpx` or `dotenv` (§1 Boundary); key
status and runs come from `tesselint` CLI subprocesses.
"""

from tesselint.server.app import ServeConfig, create_app
from tesselint.server.cases import build_cases
from tesselint.server.decisions import DecisionStore
from tesselint.server.diff import diff_runs
from tesselint.server.export import build_export, export_markdown_doc
from tesselint.server.guard import LocalGuard
from tesselint.server.jobs import JobManager
from tesselint.server.runindex import RunEntry, scan_runs
from tesselint.server.runstore import RunStore

__all__ = [
    "DecisionStore",
    "JobManager",
    "LocalGuard",
    "RunEntry",
    "RunStore",
    "ServeConfig",
    "build_cases",
    "build_export",
    "create_app",
    "diff_runs",
    "export_markdown_doc",
    "scan_runs",
]
