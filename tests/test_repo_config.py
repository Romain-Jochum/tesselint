"""Repository hardening the CI and the release rely on: pinned actions, Dependabot, ignores."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CI = REPO / ".github" / "workflows" / "ci.yml"
DEPENDABOT = REPO / ".github" / "dependabot.yml"


def _steps(workflow: str) -> list[str]:
    """The workflow's list items (steps and others), each from its `- ` to the next one."""
    return re.split(r"^\s*- ", workflow, flags=re.MULTILINE)[1:]


def test_third_party_actions_are_pinned_to_a_commit() -> None:
    """A moved tag cannot change what CI runs; first-party `actions/*` may keep their tags."""
    uses = re.findall(r"^\s*(?:- )?uses:\s*(\S+)", CI.read_text("utf-8"), re.MULTILINE)
    third_party = [u for u in uses if not u.startswith("actions/")]

    assert third_party != []
    assert [u for u in third_party if not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u)] == []


def test_checkouts_do_not_persist_the_token() -> None:
    """No checkout leaves the job's GITHUB_TOKEN in .git/config for the later steps."""
    checkouts = [
        s for s in _steps(CI.read_text("utf-8")) if s.startswith("uses: actions/checkout@")
    ]

    assert len(checkouts) == 3
    assert all(re.search(r"^\s+persist-credentials: false$", s, re.MULTILINE) for s in checkouts)


def test_the_python_job_downloads_the_model_on_a_cache_miss_before_the_tests() -> None:
    """The slow tests load model2vec offline: an empty CI cache must be filled online first."""
    python_job = CI.read_text("utf-8").split("\n  web:")[0]
    steps = _steps(python_job)
    [cache] = [i for i, s in enumerate(steps) if "path: ~/.cache/huggingface" in s]
    [tests] = [i for i, s in enumerate(steps) if "pytest" in s]
    download = steps[cache + 1]

    assert re.search(r"^\s+id: hf-cache$", steps[cache], re.MULTILINE)
    assert "if: steps.hf-cache.outputs.cache-hit != 'true'" in download
    assert "StaticModel.from_pretrained('minishlab/potion-retrieval-32M')" in download
    assert "HF_HUB_OFFLINE" not in python_job
    assert cache + 1 < tests


def test_dependabot_watches_uv_npm_and_actions_monthly() -> None:
    """Version updates for the Python lock, the web lock and the workflow actions."""
    entries = [
        s for s in _steps(DEPENDABOT.read_text("utf-8")) if s.startswith("package-ecosystem")
    ]
    found = set()
    for entry in entries:
        ecosystem = re.search(r'^package-ecosystem: "?([\w-]+)"?$', entry, re.MULTILINE)
        directory = re.search(r'^\s+directory: "?([^"\s]+)"?$', entry, re.MULTILINE)
        interval = re.search(r'^\s+interval: "?(\w+)"?$', entry, re.MULTILINE)
        assert ecosystem and directory and interval, entry
        found.add((ecosystem[1], directory[1], interval[1]))

    assert found == {
        ("uv", "/", "monthly"),
        ("npm", "/web", "monthly"),
        ("github-actions", "/", "monthly"),
    }


def test_local_build_and_report_dirs_are_ignored() -> None:
    """Playwright's blob reports, a stray root node_modules and Vite's cache stay untracked."""
    lines = set((REPO / ".gitignore").read_text("utf-8").splitlines())

    assert {"web/blob-report/", "/node_modules/", "web/.vite/"} <= lines
