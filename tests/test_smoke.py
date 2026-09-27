"""Every module imports and exposes the public names of DESIGN §8."""

import importlib

import pytest

PUBLIC_NAMES: dict[str, tuple[str, ...]] = {
    "tesselint": ("__version__",),
    "tesselint.models": (
        "Relation",
        "ProposalKind",
        "Order",
        "Story",
        "Tombstone",
        "Snapshot",
        "ParseResult",
        "Thresholds",
    ),
    "tesselint.config": (
        "DEFAULT_BASE_URL",
        "DEFAULT_MODEL",
        "USD_PER_TOKEN",
        "TIERS",
        "RunConfig",
    ),
    "tesselint.cli": ("main",),
    "tesselint.parse": ("take_snapshot", "load_snapshot", "parse_section", "select_epics"),
    "tesselint.render": ("clean", "render", "text_hash"),
    "tesselint.questions": ("Question", "REL_V1", "ATOMIC_V2"),
    "tesselint.jev": (
        "Answer",
        "JevError",
        "JevRetryable",
        "JevFatal",
        "JevClient",
        "parse_response",
    ),
    "tesselint.cache": ("cache_key", "AnswerCache"),
    "tesselint.workers": (
        "Job",
        "JobResult",
        "RetryPolicy",
        "SpendLedger",
        "RunStats",
        "run_jobs",
        "estimate_tokens",
    ),
    "tesselint.prefilter": ("similarity", "keep_pairs"),
    "tesselint.verdicts": ("PairVerdict", "build_verdicts", "overlap_matrix"),
    "tesselint.cluster": ("Cluster", "average_linkage"),
    "tesselint.proposals": ("Proposal", "propose", "render_proposals", "Thresholds"),
    "tesselint.synth": ("Truth", "generate"),
    "tesselint.evaluate": ("PR", "score_synthetic", "make_label_sheet", "score_labels"),
    "tesselint.pipeline": ("run", "report"),
    "tesselint.outputs": ("write_outputs",),
}


@pytest.mark.parametrize(("module_name", "names"), sorted(PUBLIC_NAMES.items()))
def test_module_exposes_public_names(module_name: str, names: tuple[str, ...]) -> None:
    module = importlib.import_module(module_name)

    missing = [name for name in names if not hasattr(module, name)]

    assert missing == []


def test_thresholds_is_one_type_shared_by_models_config_and_proposals() -> None:
    models = importlib.import_module("tesselint.models")
    config = importlib.import_module("tesselint.config")
    proposals = importlib.import_module("tesselint.proposals")

    assert config.Thresholds is models.Thresholds
    assert proposals.Thresholds is models.Thresholds
