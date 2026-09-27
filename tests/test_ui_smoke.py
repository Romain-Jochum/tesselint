"""Every web-UI and input-v1 module imports and exposes the names of docs/ui/DESIGN.md §7.1."""

import dataclasses
import importlib
import inspect

import pytest

UI_NAMES: dict[str, tuple[str, ...]] = {
    "tesselint.__main__": ("main",),
    "tesselint.proposals": ("proposal_key", "Proposal"),
    "tesselint.parse": ("parse_snapshot", "epic_names"),
    "tesselint.jev": ("KeyInfo", "JevClient"),
    "tesselint.pipeline": ("run", "derive", "Derived"),
    "tesselint.derive": ("derive", "Derived"),
    "tesselint.protocol": ("KeyStatus", "EstimateLine", "ProgressEvent"),
    "tesselint.inputv1": (
        "load_v1",
        "validate_v1",
        "InputProblem",
        "to_parse_result",
        "import_markdown",
        "export_markdown",
    ),
    "tesselint.server": (
        "ServeConfig",
        "create_app",
        "LocalGuard",
        "scan_runs",
        "RunEntry",
        "RunStore",
        "build_cases",
        "diff_runs",
        "DecisionStore",
        "build_export",
        "export_markdown_doc",
        "JobManager",
    ),
    "tesselint.server.app": ("ServeConfig", "create_app"),
    "tesselint.server.guard": ("LocalGuard",),
    "tesselint.server.runindex": ("scan_runs", "RunEntry"),
    "tesselint.server.runstore": ("RunStore",),
    "tesselint.server.cases": ("build_cases",),
    "tesselint.server.diff": ("diff_runs",),
    "tesselint.server.decisions": ("DecisionStore",),
    "tesselint.server.export": ("build_export", "export_markdown_doc"),
    "tesselint.server.jobs": ("JobManager",),
    "tesselint.server.payloads": ("RunBundle", "ProposalsResponse", "DecisionsResponse", "Job"),
    "tesselint.server.errors": ("ErrorCode", "ApiError"),
}

FIELDS: dict[str, tuple[str, ...]] = {
    "tesselint.derive.Derived": (
        "stories",
        "verdicts",
        "clusters",
        "atomic",
        "proposals",
        "thresholds",
    ),
    "tesselint.jev.KeyInfo": ("usage_usd", "limit_usd"),
    "tesselint.inputv1.InputProblem": ("pointer", "story_id", "message", "severity"),
    "tesselint.server.app.ServeConfig": (
        "host",
        "port",
        "runs_root",
        "state_dir",
        "sources",
        "cli",
        "ui_max_usd",
        "token",
        "static_dir",
    ),
    "tesselint.server.runindex.RunEntry": (
        "ref",
        "path",
        "meta",
        "kind",
        "kind_inferred",
        "dry_run",
    ),
}

METHODS: dict[str, tuple[str, ...]] = {
    "tesselint.jev.JevClient": ("key_info",),
    "tesselint.server.runstore.RunStore": ("entries", "bundle", "matrix", "proposals", "pair"),
    "tesselint.server.decisions.DecisionStore": ("read", "apply", "undo", "views"),
    "tesselint.server.jobs.JobManager": ("estimate", "start", "get", "cancel", "active"),
}


def _resolve(dotted: str) -> object:
    module_name, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module_name), name)


@pytest.mark.parametrize(("module_name", "names"), sorted(UI_NAMES.items()))
def test_module_exposes_ui_names(module_name: str, names: tuple[str, ...]) -> None:
    """§7.1: each new or extended module imports and has its names."""
    module = importlib.import_module(module_name)

    assert [name for name in names if not hasattr(module, name)] == []


@pytest.mark.parametrize(("dotted", "fields"), sorted(FIELDS.items()))
def test_dataclass_has_fields_in_order(dotted: str, fields: tuple[str, ...]) -> None:
    """§7.1: the data classes carry exactly the listed fields, in order."""
    cls = _resolve(dotted)

    assert isinstance(cls, type)
    assert dataclasses.is_dataclass(cls)
    assert tuple(f.name for f in dataclasses.fields(cls)) == fields


@pytest.mark.parametrize(("dotted", "methods"), sorted(METHODS.items()))
def test_class_has_methods(dotted: str, methods: tuple[str, ...]) -> None:
    """§7.1: the stateful classes expose the listed methods."""
    cls = _resolve(dotted)

    assert [m for m in methods if not callable(getattr(cls, m, None))] == []


def test_proposal_gains_defaulted_key_and_details() -> None:
    """§7.1: `Proposal.key` defaults to "" and `Proposal.details` to None (additive)."""
    from tesselint.proposals import Proposal

    defaults = {f.name: f.default for f in dataclasses.fields(Proposal)}

    assert defaults["key"] == ""
    assert defaults["details"] is None


def test_pipeline_run_accepts_events_keyword() -> None:
    """§7.1: `pipeline.run` takes a keyword-only `events` callback defaulting to None."""
    from tesselint import pipeline

    param = inspect.signature(pipeline.run).parameters["events"]

    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None
