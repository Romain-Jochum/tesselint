"""Stories v1 input: schema and cross-field rules, CLI errors, snapshots, import and export.

Spec: docs/input/INPUT.md §2-§5 and docs/ui/DESIGN.md §4.5, §9 (status defaults to live). The 12
broken conversions are INPUT.md §3's, each with the message its rule gives; schema
messages are jsonschema's own (4.26, locked). A CLI problem line reads
`<file>: $.stories[<i>] (<id>).<field>: <message>` (INPUT.md §4).
"""

import copy
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tesselint.cli import main
from tesselint.inputv1 import (
    SCHEMA_PATH,
    InputProblem,
    export_markdown,
    import_markdown,
    load_v1,
    to_parse_result,
    validate_v1,
)
from tesselint.models import ParseResult, Story, Tombstone
from tesselint.parse import epic_names, load_snapshot, parse_section, parse_snapshot, take_snapshot
from tesselint.render import render, text_hash
from tests.helpers_pipeline import force_hf_offline

ROOT = Path(__file__).resolve().parent.parent
Doc = dict[str, Any]
Mutation = Callable[[Doc], None]


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` must never read the real .env from these tests."""
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")


def invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the CLI in-process; (exit code, stdout, stderr). A stub's error is re-raised."""
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    out, err = capsys.readouterr()
    if code == 1 and "unexpected NotImplementedError" in err:
        raise NotImplementedError(err.strip())
    return code, out, err


def mini_doc() -> Doc:
    """A valid v1 document: two epics, three live stories and one tombstone, no warnings."""
    return {
        "format": "tesselint/stories",
        "version": 1,
        "render": "md-clean/1",
        "source": {"path": "backlog.md", "converter": "tesselint import/0.1.0"},
        "generated_at": "2026-09-26T12:00:00Z",
        "priorities": ["MVP", "MVP+", "Lite", "Later", "Not now", "decision"],
        "epics": [
            {"id": "E1", "name": "Plots", "source_line": 3},
            {"id": "E2", "name": "Tools", "source_line": 9},
        ],
        "stories": [
            {
                "id": "US-1.1",
                "epic": "E1",
                "title": "Accept a plot offer",
                "tag": "MVP",
                "priority": "MVP",
                "statement": "I accept or decline an offer within fourteen days (G10).",
                "details": ["The offer shows the plot size.", "Declining twice moves me down."],
                "status": "live",
                "source_line": 4,
            },
            {
                "id": "US-1.2",
                "epic": "E1",
                "title": "Former offer rules",
                "status": "removed",
                "merged_into": "US-1.1",
                "removed_reason": "same flow",
                "source_line": 7,
            },
            {
                "id": "US-2.1",
                "epic": "E2",
                "title": "Borrow a tool",
                "tag": "Lite, members only",
                "priority": "Lite",
                "statement": "",
                "details": ["I book a tool for a two-hour slot.", "The shed shows what is out."],
                "status": "live",
                "source_line": 10,
            },
            {
                "id": "US-2.2",
                "epic": "E2",
                "title": "Check a tool back in",
                "statement": "I scan the QR tag when I return a tool.",
                "status": "live",
                "source_line": 13,
            },
        ],
    }


# The mini document as legacy Markdown, written by hand.
MINI_MD = """## 4. User stories

### E1 Plots

- **US-1.1 Accept a plot offer [MVP].** I accept or decline an offer within fourteen days (G10).
  - The offer shows the plot size.
  - Declining twice moves me down.
- **US-1.2 Former offer rules** — merged into US-1.1: same flow

### E2 Tools

- **US-2.1 Borrow a tool [Lite, members only].**
  - I book a tool for a two-hour slot.
  - The shed shows what is out.
- **US-2.2 Check a tool back in.** I scan the QR tag when I return a tool.
"""


def write_doc(path: Path, doc: Doc) -> Path:
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def mutated(fn: Mutation) -> Doc:
    doc = mini_doc()
    fn(doc)
    return doc


def errors(problems: list[InputProblem]) -> list[InputProblem]:
    return [p for p in problems if p.severity == "error"]


def located(pointer: str, story_id: str | None) -> str:
    """INPUT.md §4: the story's ID follows its index, `$.stories[12] (US-2.3).status`."""
    at = re.match(r"^\$\.stories\[\d+\]", pointer)
    if at is None or story_id is None:
        return pointer
    return f"{at.group(0)} ({story_id}){pointer[at.end() :]}"


def story_key(s: Story) -> tuple[object, ...]:
    return (s.id, s.epic, s.title, s.tag, s.core, s.subs)


def without_lines(doc: Doc) -> Doc:
    """The document's epics and stories without `source_line`, which export renumbers."""

    def strip(rows: list[Doc]) -> list[Doc]:
        return [{k: v for k, v in row.items() if k != "source_line"} for row in rows]

    return {"epics": strip(doc["epics"]), "stories": strip(doc["stories"])}


def section_of(document: str) -> str:
    """§4 of a synth document (the part `take_snapshot` keeps)."""
    return document[document.index("## 4.") : document.index("\n## 5.") + 1]


# (name, mutation, pointer, story_id or None, message): the broken conversions of INPUT.md §3.
MUTATIONS: list[tuple[str, Mutation, str, str | None, str]] = [
    (
        "duplicate id",
        lambda d: d["stories"].append(copy.deepcopy(d["stories"][0])),
        "$.stories",
        None,
        "duplicate id US-1.1",
    ),
    (
        "missing statement",
        lambda d: d["stories"][0].pop("statement"),
        "$.stories[0]",
        "US-1.1",
        "'statement' is a required property",
    ),
    (
        "unknown epic",
        lambda d: d["stories"][0].update(epic="E999"),
        "$.stories[0].epic",
        "US-1.1",
        "unknown epic E999",
    ),
    (
        "bad status",
        lambda d: d["stories"][0].update(status="alive"),
        "$.stories[0].status",
        "US-1.1",
        "'alive' is not one of ['live', 'removed']",
    ),
    (
        "tag left in title",
        lambda d: d["stories"][0].update(title="Foo [MVP]"),
        "$.stories[0].title",
        "US-1.1",
        r"'Foo [MVP]' should not be valid under {'pattern': '[\\[\\]]'}",
    ),
    (
        "multi-line detail",
        lambda d: d["stories"][0].update(details=["a\nb"]),
        "$.stories[0].details[0]",
        "US-1.1",
        r"'a\nb' does not match '^\\S(?:[^\\r\\n]*\\S)?$'",
    ),
    (
        "merged_into on live",
        lambda d: d["stories"][0].update(merged_into="US-1.2"),
        "$.stories[0]",
        "US-1.1",
        "False schema does not allow 'US-1.2'",
    ),
    (
        "dangling merge",
        lambda d: d["stories"][1].update(merged_into="US-99.9"),
        "$.stories[1].merged_into",
        "US-1.2",
        "US-99.9 does not lead to a live story",
    ),
    (
        "unknown field",
        lambda d: d["stories"][0].update(acceptance=["x"]),
        "$.stories[0]",
        "US-1.1",
        "'acceptance' does not match any of the regexes: '^x-'",
    ),
    (
        "priority off-vocab",
        lambda d: d["stories"][0].update(priority="Must"),
        "$.stories[0].priority",
        "US-1.1",
        "'Must' not in priorities",
    ),
    (
        "wrong version",
        lambda d: d.update(version=2),
        "$.version",
        None,
        "1 was expected",
    ),
    (
        "padded statement",
        lambda d: d["stories"][0].update(statement=" I do x "),
        "$.stories[0].statement",
        "US-1.1",
        r"' I do x ' does not match '^(?:\\S(?:[^\\r\\n]*\\S)?)?$'",
    ),
]
MUTATION_PARAMS = [pytest.param(*m[1:], id=m[0]) for m in MUTATIONS]


# --- schema and cross-field rules -----------------------------------------------------------------


def test_packaged_schema_is_byte_equal_to_the_schemas_dir() -> None:
    """DESIGN §4.5: the schema ships as package data, byte-equal to schemas/."""
    assert SCHEMA_PATH.read_bytes() == (ROOT / "schemas" / "stories.v1.schema.json").read_bytes()


def test_a_valid_document_has_no_problems(tmp_path: Path) -> None:
    doc = mini_doc()
    path = write_doc(tmp_path / "stories.json", doc)

    assert load_v1(path) == doc
    assert validate_v1(doc) == []


@pytest.mark.parametrize(("fn", "pointer", "story_id", "message"), MUTATION_PARAMS)
def test_a_broken_conversion_is_an_error_with_its_message(
    fn: Mutation, pointer: str, story_id: str | None, message: str
) -> None:
    """INPUT.md §3: 12/12 deliberately broken conversions are rejected, each by its rule."""
    found = errors(validate_v1(mutated(fn)))

    assert (pointer, message) in [(p.pointer, p.message) for p in found]
    if story_id is not None:
        assert story_id in [p.story_id for p in found if p.pointer == pointer]


@pytest.mark.parametrize(("fn", "pointer", "story_id", "message"), MUTATION_PARAMS)
def test_parse_prints_one_line_per_problem_and_exits_4(
    fn: Mutation,
    pointer: str,
    story_id: str | None,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INPUT.md §4: `file: path: message` per problem, then the count line, exit 4."""
    monkeypatch.chdir(tmp_path)
    write_doc(tmp_path / "stories.json", mutated(fn))

    code, _, err = invoke(["parse", "--source", "stories.json"], capsys)

    lines = err.splitlines()
    problems = [line for line in lines if line.startswith("stories.json: $")]
    assert code == 4
    assert f"stories.json: {located(pointer, story_id)}: {message}" in problems
    assert lines[-1] == (
        f"tesselint: error: {len(problems)} invalid field(s) in stories.json; fix them first"
    )


def test_x_extension_fields_are_accepted() -> None:
    """INPUT.md §2 Extensions: any object may carry `x-*` keys."""
    doc = mini_doc()
    doc["x-note"] = "extension"
    doc["source"]["x-tool"] = "jira"
    doc["epics"][0]["x-jira-epic"] = "PRJ-E1"
    doc["stories"][0]["x-jira-key"] = "PRJ-1"

    assert errors(validate_v1(doc)) == []


def test_status_defaults_to_live() -> None:
    """DESIGN §9: a story without `status` is live (the schema keeps it optional)."""
    doc = mini_doc()
    del doc["stories"][3]["status"]

    assert errors(validate_v1(doc)) == []
    assert "US-2.2" in [s.id for s in to_parse_result(doc).stories]


def test_generated_at_must_be_a_date_time() -> None:
    """INPUT.md §4: `generated_at` is checked with datetime.fromisoformat."""
    doc = mini_doc()
    doc["generated_at"] = "yesterday"

    assert "$.generated_at" in [p.pointer for p in errors(validate_v1(doc))]


WARNINGS: list[tuple[str, Mutation, tuple[str, ...]]] = [
    (
        "title only",
        lambda d: d["stories"][3].update(statement=""),
        ("US-2.2", "no statement and no details"),
    ),
    (
        "epic without live stories",
        lambda d: d["epics"].append({"id": "E3", "name": "Water"}),
        ("E3", "no live story"),
    ),
    (
        "same rendered text",
        lambda d: d["stories"][3].update(
            {k: d["stories"][0][k] for k in ("title", "statement", "details")}
        ),
        ("US-2.2", "same text as US-1.1"),
    ),
    (
        "unknown reference",
        lambda d: d["stories"][0].update(references=["US-9.9"]),
        ("US-9.9", "is not in the document"),
    ),
]


@pytest.mark.parametrize(
    ("fn", "needles"), [pytest.param(fn, needles, id=name) for name, fn, needles in WARNINGS]
)
def test_cross_field_warnings(fn: Mutation, needles: tuple[str, ...]) -> None:
    """INPUT.md §2 Rules: warnings, not errors."""
    problems = validate_v1(mutated(fn))

    assert errors(problems) == []
    warned = [f"{p.pointer} {p.story_id} {p.message}" for p in problems]
    assert any(all(n in w for n in needles) for w in warned), warned


def test_parse_prints_warnings_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """INPUT.md §4: warnings print; exit 0; the counts are those of the markdown parser."""
    monkeypatch.chdir(tmp_path)
    write_doc(tmp_path / "stories.json", mutated(WARNINGS[0][1]))

    code, out, err = invoke(["parse", "--source", "stories.json"], capsys)

    assert code == 0, err
    assert "3 stories, 1 tombstones" in out
    assert "no statement and no details" in out + err


def test_malformed_json_is_one_line_with_line_and_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """INPUT.md §4: malformed JSON is one line naming the line and column, exit 4."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stories.json").write_text(
        '{\n  "format": "tesselint/stories",\n  "version": 1\n  "epics": []\n}\n', "utf-8"
    )

    code, _, err = invoke(["parse", "--source", "stories.json"], capsys)

    [line] = err.splitlines()
    assert code == 4
    assert line.startswith("tesselint: error:")
    assert "stories.json" in line
    assert re.search(r"line 4\b", line) and re.search(r"column 3\b", line), line


# --- mapping and snapshots ------------------------------------------------------------------------


def test_to_parse_result_maps_fields() -> None:
    """DESIGN §4.5: statement -> core, details -> subs, tag or "", source_line -> line."""
    expected = ParseResult(
        stories=(
            Story(
                "US-1.1",
                "E1",
                "Accept a plot offer",
                "MVP",
                "I accept or decline an offer within fourteen days (G10).",
                ("The offer shows the plot size.", "Declining twice moves me down."),
                4,
            ),
            Story(
                "US-2.1",
                "E2",
                "Borrow a tool",
                "Lite, members only",
                "",
                ("I book a tool for a two-hour slot.", "The shed shows what is out."),
                10,
            ),
            Story(
                "US-2.2",
                "E2",
                "Check a tool back in",
                "",
                "I scan the QR tag when I return a tool.",
                (),
                13,
            ),
        ),
        tombstones=(Tombstone("US-1.2", "E1", "Former offer rules", "US-1.1", 7),),
        unparsed=(),
    )

    assert to_parse_result(mini_doc()) == expected


@pytest.mark.parametrize("fmt", ["md", "json"])
def test_load_snapshot_loads_whichever_snapshot_exists(tmp_path: Path, fmt: str) -> None:
    """DESIGN §4.5: `load_snapshot` loads snapshot.md or snapshot.json; `parse_snapshot` maps it."""
    doc = mini_doc()
    want = parse_section(MINI_MD) if fmt == "md" else to_parse_result(doc)
    data = MINI_MD.encode("utf-8") if fmt == "md" else json.dumps(doc, indent=1).encode("utf-8")
    name = f"snapshot.{fmt}"
    (tmp_path / name).write_bytes(data)
    (tmp_path / "snapshot.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {name}\n")

    snapshot = load_snapshot(tmp_path)
    parsed = parse_snapshot(snapshot)

    assert snapshot.path == tmp_path / name
    assert snapshot.sha256 == hashlib.sha256(data).hexdigest()
    assert parsed == want


def test_epic_names_are_equal_for_markdown_and_json_snapshots(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN §7.1: `epic_names` gives (id, heading name) in narrative order, either format."""
    for fmt in ("md", "json"):
        argv = ["synth", "--out", str(tmp_path / fmt), "--n", "24", "--format", fmt]
        assert invoke(argv, capsys)[0] == 0
    md_snapshot = take_snapshot(tmp_path / "md" / "stories.md", tmp_path / "md-run")
    data = (tmp_path / "json" / "stories.json").read_bytes()
    run = tmp_path / "json-run"
    run.mkdir()
    (run / "snapshot.json").write_bytes(data)
    (run / "snapshot.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  snapshot.json\n")

    names = epic_names(md_snapshot)

    assert names == epic_names(load_snapshot(run))
    assert len(names) == 8
    assert names[0] == ("E1", "Waiting list and plot allocation")


@pytest.mark.slow
def test_a_run_copies_a_json_source_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN §4.5: a JSON source becomes snapshot.json byte for byte, with its sha256."""
    force_hf_offline(monkeypatch)
    monkeypatch.chdir(tmp_path)
    doc = mini_doc() | {"x-note": "déjà vu, indented by three"}
    parsed = to_parse_result(doc)
    data = json.dumps(doc, indent=3, ensure_ascii=False).encode("utf-8")  # not a canonical form
    (tmp_path / "stories.json").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()

    code, _, err = invoke(["run", "--source", "stories.json", "--out", "runs", "--dry-run"], capsys)

    assert code == 0, err
    [run_dir] = list((tmp_path / "runs").iterdir())
    assert (run_dir / "snapshot.json").read_bytes() == data
    assert (run_dir / "snapshot.sha256").read_text("utf-8") == f"{sha}  snapshot.json\n"
    assert not (run_dir / "snapshot.md").exists()
    meta = json.loads((run_dir / "run.json").read_text("utf-8"))
    assert (meta["snapshot_sha256"], meta["n_stories"]) == (sha, len(parsed.stories))
    assert meta["n_tombstones"] == len(parsed.tombstones)


@pytest.mark.slow
def test_a_document_in_the_legacy_format_still_reads_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Runs made before the rename pin their snapshot.json bytes: the old ids still read."""
    force_hf_offline(monkeypatch)
    monkeypatch.chdir(tmp_path)
    old_format, old_synth = "story-sim/stories", "story-sim synth/1"  # ids from before the rename
    doc = mini_doc() | {"format": old_format, "source": {"path": "synth", "converter": old_synth}}
    path = write_doc(tmp_path / "stories.json", doc)

    loaded = load_v1(path)
    assert loaded["format"] == "tesselint/stories"
    assert validate_v1(loaded) == []
    assert to_parse_result(loaded) == to_parse_result(mini_doc())

    code, _, err = invoke(["run", "--source", "stories.json", "--out", "runs", "--dry-run"], capsys)

    assert code == 0, err
    [run_dir] = list((tmp_path / "runs").iterdir())
    assert (run_dir / "snapshot.json").read_bytes() == path.read_bytes()
    assert json.loads((run_dir / "run.json").read_text("utf-8"))["kind"] == "synthetic"


# --- import and export ----------------------------------------------------------------------------


def test_v1_and_markdown_give_equal_text_hashes_for_all_176_stories(real_section4: Path) -> None:
    """INPUT.md §3: 176/176 full-text hashes and core texts identical, same IDs and order."""
    legacy = parse_section(real_section4.read_text("utf-8"))
    v1 = to_parse_result(import_markdown(real_section4))

    assert len(v1.stories) == len(legacy.stories) == 176
    assert [s.id for s in v1.stories] == [s.id for s in legacy.stories]
    assert [text_hash(render(s)) for s in v1.stories] == [
        text_hash(render(s)) for s in legacy.stories
    ]
    assert [render(s, "core") for s in v1.stories] == [render(s, "core") for s in legacy.stories]
    assert [(t.id, t.target) for t in v1.tombstones] == [
        (t.id, t.target) for t in legacy.tombstones
    ]


def test_import_of_section4_is_valid_with_one_warning(real_section4: Path) -> None:
    """INPUT.md §3: 0 schema errors and 1 warning (US-18.5, no statement and no details)."""
    doc = import_markdown(real_section4)

    problems = validate_v1(doc)

    assert errors(problems) == []
    assert [p.story_id for p in problems] == ["US-18.5"]
    assert doc["source"]["sha256"] == (
        "b2dd2ef8cf7bd67fe151a1ca270f310efe3691fc58a41b1d7f1208a90611b14c"
    )
    assert sum(s["status"] == "removed" for s in doc["stories"]) == 3


def test_import_export_import_round_trips(real_section4: Path, tmp_path: Path) -> None:
    """INPUT.md §1, §3: v1 -> Markdown -> v1 is identical; the parser reads the same stories."""
    doc = import_markdown(real_section4)
    markdown = export_markdown(doc)
    exported = tmp_path / "exported.md"
    exported.write_text(markdown, encoding="utf-8")

    again = import_markdown(exported)

    assert without_lines(again) == without_lines(doc)
    original = parse_section(real_section4.read_text("utf-8"))
    reparsed = parse_section(markdown)
    assert reparsed.unparsed == ()
    assert [story_key(s) for s in reparsed.stories] == [story_key(s) for s in original.stories]


def test_cli_import_export_and_parse(
    real_section4: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INPUT.md §4: `import SOURCE.md --out F`, `export F`, `parse --source F`."""
    monkeypatch.chdir(tmp_path)

    import_code, _, import_err = invoke(
        ["import", str(real_section4), "--out", "stories.json"], capsys
    )
    doc = load_v1(tmp_path / "stories.json")
    export_code, export_out, _ = invoke(["export", "stories.json"], capsys)
    parse_code, parse_out, parse_err = invoke(["parse", "--source", "stories.json"], capsys)

    assert import_code == 0, import_err
    assert errors(validate_v1(doc)) == []
    assert export_code == 0
    assert export_out.rstrip("\n") == export_markdown(doc).rstrip("\n")
    assert parse_code == 0, parse_err
    assert "176 stories, 3 tombstones" in parse_out
    assert "US-18.5" in parse_out + parse_err


def test_synth_json_is_the_synth_markdown_as_v1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESIGN §4.5: `synth --format json` writes the same stories as v1, marked x-synthetic."""
    for fmt in ("md", "json"):
        argv = ["synth", "--out", str(tmp_path / fmt), "--n", "24", "--seed", "42"]
        assert invoke([*argv, "--format", fmt], capsys)[0] == 0

    doc = load_v1(tmp_path / "json" / "stories.json")
    legacy = parse_section(section_of((tmp_path / "md" / "stories.md").read_text("utf-8")))
    v1 = to_parse_result(doc)

    assert doc["x-synthetic"] is True
    assert errors(validate_v1(doc)) == []
    assert [story_key(s) for s in v1.stories] == [story_key(s) for s in legacy.stories]
    assert [text_hash(render(s)) for s in v1.stories] == [
        text_hash(render(s)) for s in legacy.stories
    ]
    assert [(t.id, t.target) for t in v1.tombstones] == [
        (t.id, t.target) for t in legacy.tombstones
    ]
    md_truth = (tmp_path / "md" / "truth.json").read_bytes()
    assert (tmp_path / "json" / "truth.json").read_bytes() == md_truth
