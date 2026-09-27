"""Stories v1 input: load, validate, map to the parser's types, import and export Markdown.

Spec: docs/input/INPUT.md §2 to §4 and docs/ui/DESIGN.md §4.5, which the converter and the
validator implement. Text fields hold the source's words verbatim and the Jev text is still made
by `render()` from the mapped `Story`, so a v1 story sends the same text, and hits the same cache
key, as its Markdown line (INPUT.md §3). The schema ships as package data, byte-equal to schemas/.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from tesselint import __version__
from tesselint.errors import InputError
from tesselint.models import ParseResult, Story, Tombstone
from tesselint.parse import EPIC_HEADING, STORY_ID, TOMBSTONE, parse_section, split_head
from tesselint.render import render, text_hash

FORMAT = "tesselint/stories"
LEGACY_FORMATS = {"story-sim/stories"}  # read as FORMAT: old runs pin their snapshot.json bytes
VERSION = 1
RENDER_PROFILE = "md-clean/1"  # INPUT.md §3; the only render profile
DEFAULT_STATUS = "live"  # a story without `status` is live (docs/ui/DESIGN.md §9)
PRIORITIES = ("MVP", "MVP+", "Lite", "Later", "Not now", "decision")  # INPUT.md §2, rank order
SCHEMA_PATH = Path(__file__).parent / "data" / "stories.v1.schema.json"
SYNTHETIC_KEY = "x-synthetic"  # set by `synth --format json` (docs/ui/DESIGN.md §4.2)
CONVERTER = f"tesselint import/{__version__}"  # source.converter of `import_markdown`
SECTION = "## 4."  # the stories section of a legacy backlog
EXPORT_HEADING = "## 4. User stories"

_LEGACY_STORY_ID = re.compile(rf"^{STORY_ID}$")
_LEGACY_EPIC_ID = re.compile(r"^E\d+$")
_STORY_POINTER = re.compile(r"^\$\.stories\[\d+\]")
# `(US-1.19, 7.1)`: a cited ID followed by short forms that share its prefix.
_REFERENCE_GROUP = re.compile(rf"\((?:from\s+)?{STORY_ID}(?:,\s*(?:from\s+)?(?:US-)?[0-9.]+)*\)")
_SHORT_ID = re.compile(r"(?<![\w.-])([0-9]+(?:\.[0-9]+)+)")

Doc = dict[str, Any]
Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class InputProblem:
    """One schema or cross-field problem; errors exit 4, warnings print (INPUT.md §4)."""

    pointer: str  # JSON path, e.g. `$.stories[12].status`
    story_id: str | None
    message: str
    severity: Severity

    def describe(self, label: str) -> str:
        """One line: `stories.json: $.stories[12] (US-2.3).status: …`; a warning says so."""
        where = self.pointer
        at = _STORY_POINTER.match(where)
        if at is not None and self.story_id is not None:
            where = f"{at.group(0)} ({self.story_id}){where[at.end() :]}"
        kind = "warning: " if self.severity == "warning" else ""
        return f"{label}: {kind}{where}: {self.message}"


class InputProblemsError(InputError):
    """An InputError whose `lines`, one per problem, print before its own line (INPUT.md §4)."""

    def __init__(self, message: str, lines: Sequence[str]) -> None:
        super().__init__(message)
        self.lines = tuple(lines)


# --- reading and validating -----------------------------------------------------------------------


def load_v1(path: Path) -> Doc:
    """Read a v1 JSON document; malformed JSON is one line with line and column (INPUT.md §4)."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"{path}: not UTF-8 text (byte {exc.start})") from exc
    return loads_v1(text, str(path))


def loads_v1(text: str, label: str) -> Doc:
    """Parse v1 JSON text, reading a legacy format id as FORMAT; `label` names the file."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(
            f"{label}: malformed JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(doc, dict):
        raise InputError(
            f"{label}: a stories v1 document is a JSON object, not {type(doc).__name__}"
        )
    if doc.get("format") in LEGACY_FORMATS:
        doc["format"] = FORMAT
    return doc


def dumps_v1(doc: Mapping[str, Any]) -> str:
    """The written form of a v1 document: UTF-8 friendly, indented by two, newline at the end."""
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def validate_v1(doc: Mapping[str, Any]) -> list[InputProblem]:
    """Schema errors; if there are none, the cross-field rules of INPUT.md §2 (errors first)."""
    found = sorted(_validator().iter_errors(doc), key=lambda e: tuple(e.absolute_path))
    if found:
        return [
            InputProblem(e.json_path, _story_id(doc, e.absolute_path), e.message, "error")
            for e in found
        ]
    return _cross_field(doc)


def check_v1(doc: Mapping[str, Any], label: str) -> list[InputProblem]:
    """Validate `doc` and return its problems, all warnings; an error raises InputProblemsError.

    The error's `lines` hold one `describe(label)` line per error; its message is the count line
    `N invalid field(s) in <label>; fix them first` (INPUT.md §4).
    """
    problems = validate_v1(doc)
    errors = [p.describe(label) for p in problems if p.severity == "error"]
    if errors:
        raise InputProblemsError(
            f"{len(errors)} invalid field(s) in {label}; fix them first", errors
        )
    return problems


@cache
def _validator() -> Any:
    """Draft 2020-12 validator of the packaged schema; `generated_at` is checked separately."""
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text("utf-8")))


def _story_id(doc: Mapping[str, Any], path: Sequence[Any]) -> str | None:
    """The ID of the story a schema error sits in, if it has a string one."""
    stories = doc.get("stories")
    if len(path) < 2 or path[0] != "stories" or not isinstance(stories, list):
        return None
    row = stories[path[1]]
    sid = row.get("id") if isinstance(row, dict) else None
    return sid if isinstance(sid, str) else None


def _cross_field(doc: Mapping[str, Any]) -> list[InputProblem]:
    """INPUT.md §2 Rules on a schema-valid document: errors, then warnings."""
    stories: list[Doc] = doc["stories"]
    epic_ids = [e["id"] for e in doc["epics"]]
    by_id = {s["id"]: s for s in stories}
    vocab = doc.get("priorities")
    errors: list[InputProblem] = []
    warnings: list[InputProblem] = []

    def error(pointer: str, sid: str | None, message: str) -> None:
        errors.append(InputProblem(pointer, sid, message, "error"))

    def warn(pointer: str, sid: str | None, message: str) -> None:
        warnings.append(InputProblem(pointer, sid, message, "warning"))

    try:
        datetime.fromisoformat(doc["generated_at"])
    except ValueError:
        error("$.generated_at", None, f"{doc['generated_at']!r} is not a 'date-time'")
    for x in _duplicates(epic_ids):
        error("$.epics", None, f"duplicate id {x}")
    for x in _duplicates(s["id"] for s in stories):
        error("$.stories", None, f"duplicate id {x}")

    first_with_text: dict[str, str] = {}
    for i, s in enumerate(stories):
        at, sid = f"$.stories[{i}]", s["id"]
        if s["epic"] not in epic_ids:
            error(f"{at}.epic", sid, f"unknown epic {s['epic']}")
        if vocab and "priority" in s and s["priority"] not in vocab:
            error(f"{at}.priority", sid, f"{s['priority']!r} not in priorities")
        for j, ref in enumerate(s.get("references", [])):
            if ref not in by_id:
                warn(f"{at}.references[{j}]", sid, f"{ref} is not in the document")
        if _status(s) == "removed":
            if s["merged_into"] is not None and not _leads_to_live(s, by_id):
                error(f"{at}.merged_into", sid, f"{s['merged_into']} does not lead to a live story")
            continue
        if not s["statement"] and not s.get("details"):
            warn(at, sid, "no statement and no details; Jev sees the title only")
        digest = text_hash(render(_story(s)))
        if digest in first_with_text:
            warn(at, sid, f"renders to the same text as {first_with_text[digest]}")
        else:
            first_with_text[digest] = sid

    for j, eid in enumerate(epic_ids):
        if not any(s["epic"] == eid and _status(s) == "live" for s in stories):
            warn(f"$.epics[{j}]", None, f"{eid} has no live story")
    return errors + warnings


def _duplicates(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    twice: set[str] = set()
    for x in ids:
        (twice if x in seen else seen).add(x)
    return sorted(twice)


def _leads_to_live(story: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether a removed story's merged_into chain ends at a live story (no cycle, no gap)."""
    seen = {story["id"]}
    target = story["merged_into"]
    while target in by_id and _status(by_id[target]) == "removed" and target not in seen:
        seen.add(target)
        target = by_id[target]["merged_into"]
    return target in by_id and target not in seen


# --- mapping to the parser's types ----------------------------------------------------------------


def _status(story: Mapping[str, Any]) -> str:
    return str(story.get("status", DEFAULT_STATUS))


def _story(s: Mapping[str, Any]) -> Story:
    """INPUT.md §4: statement → core, details → subs, tag or "", source_line → line."""
    return Story(
        id=s["id"],
        epic=s["epic"],
        title=s["title"],
        tag=s.get("tag") or "",
        core=s["statement"],
        subs=tuple(s.get("details", ())),
        line=s.get("source_line", 0),
    )


def to_parse_result(doc: Mapping[str, Any]) -> ParseResult:
    """Map a valid v1 document to Story and Tombstone (statement→core, details→subs; §4.5)."""
    rows = doc["stories"]
    return ParseResult(
        stories=tuple(_story(s) for s in rows if _status(s) == "live"),
        tombstones=tuple(
            Tombstone(
                id=s["id"],
                epic=s["epic"],
                title=s["title"],
                target=s["merged_into"],
                line=s.get("source_line", 0),
            )
            for s in rows
            if _status(s) == "removed"
        ),
        unparsed=(),
    )


# --- legacy Markdown -> v1 ------------------------------------------------------------------------


def import_markdown(source: Path, *, section: str = SECTION) -> Doc:
    """Convert a legacy Markdown backlog's `section` to v1, deterministically (INPUT.md §4)."""
    return convert_markdown(
        source.read_bytes(), path=str(source), converter=CONVERTER, section=section
    )


def convert_markdown(raw: bytes, *, path: str, converter: str, section: str = SECTION) -> Doc:
    """The v1 document of `section` in Markdown bytes, word for word (INPUT.md §2, §5).

    Stories and tombstones come from `parse_section`, so every live story maps back to the Story
    the legacy parser reads. The section runs from the first line starting with `section` to the
    next heading of its level (the whole text if there is no such line). Unparsed `- **US-` lines
    raise InputProblemsError, one line each.
    """
    body, offset, found = _cut_section(raw, section)
    text = body.decode("utf-8")
    parsed = parse_section(text)
    if parsed.unparsed:
        lines = [f"{path}:{n + offset}: unparsed: {line}" for n, line in parsed.unparsed]
        raise InputProblemsError(
            f"{len(lines)} unparsed story line(s) in {path}; fix them first", lines
        )
    lines = text.splitlines()
    source: Doc = {"path": path}
    if found:
        source["section"] = lines[0].strip()
    source |= {
        "sha256": hashlib.sha256(body).hexdigest(),
        "line_offset": offset,
        "converter": converter,
    }
    rows = [(s.line, _live_row(s, offset)) for s in parsed.stories]
    rows += [(t.line, _removed_row(t, lines, offset)) for t in parsed.tombstones]
    return {
        "format": FORMAT,
        "version": VERSION,
        "render": RENDER_PROFILE,
        "source": source,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "priorities": list(PRIORITIES),
        "epics": [
            {"id": m[1], "name": m[2], "source_line": i + 1 + offset}
            for i, line in enumerate(lines)
            if (m := EPIC_HEADING.match(line.strip()))
        ],
        "stories": [row for _, row in sorted(rows, key=lambda r: r[0])],
    }


def _cut_section(raw: bytes, heading: str) -> tuple[bytes, int, bool]:
    """(section bytes, lines before it, whether the heading was found)."""
    start = re.search(rb"^" + re.escape(heading.encode("utf-8")), raw, re.MULTILINE)
    if start is None:
        return raw, 0, False
    level = max(1, len(heading) - len(heading.lstrip("#")))
    newline = raw.find(b"\n", start.start())
    next_heading = re.compile(rb"^#{1,%d}\s" % level, re.MULTILINE)
    end = next_heading.search(raw, newline + 1) if newline >= 0 else None
    body = raw[start.start() : end.start() if end else len(raw)]
    return body, raw[: start.start()].count(b"\n"), True


def _live_row(s: Story, offset: int) -> Doc:
    row: Doc = {"id": s.id, "epic": s.epic, "title": s.title, **_tag_fields(s.tag)}
    row |= {"statement": s.core, "details": list(s.subs), "status": "live"}
    if refs := _references(s.id, [s.core, *s.subs]):
        row["references"] = refs
    return row | {"source_line": s.line + offset}


def _removed_row(t: Tombstone, lines: Sequence[str], offset: int) -> Doc:
    """A tombstone with its tag split out of the title (parse.py keeps it, INPUT.md §6.4)."""
    m = TOMBSTONE.match(lines[t.line - 1].strip())
    assert m is not None, t  # parse_section matched this very line with TOMBSTONE
    title, tag = split_head(m[2])
    row: Doc = {"id": t.id, "epic": t.epic, "title": title, **_tag_fields(tag)}
    row |= {"status": "removed", "merged_into": t.target}
    if reason := (m[4] or "").strip():
        row["removed_reason"] = reason
    return row | {"source_line": t.line + offset}


def _tag_fields(tag: str) -> Doc:
    """`tag` verbatim and, if it leads with a known keyword, `priority`."""
    if not tag:
        return {}
    priority = next(
        (
            p
            for p in sorted(PRIORITIES, key=len, reverse=True)
            if re.match(re.escape(p) + r"(?=$|[\s:;,])", tag)
        ),
        None,
    )
    return {"tag": tag} | ({"priority": priority} if priority else {})


def _references(sid: str, texts: Sequence[str]) -> list[str]:
    """Story IDs cited in the texts, short forms `(US-1.19, 7.1)` expanded, first-seen order."""
    found: list[str] = []
    for text in texts:
        for group in _REFERENCE_GROUP.findall(text):
            found += ["US-" + n for n in _SHORT_ID.findall(group)]
        found += re.findall(STORY_ID, text)
    return list(dict.fromkeys(r for r in found if r != sid))


# --- v1 -> legacy Markdown ------------------------------------------------------------------------


def export_markdown(doc: Mapping[str, Any]) -> str:
    """Write a v1 document as legacy Markdown that parses back identically (INPUT.md §4).

    Stories are written under their epic's heading. `x-*` keys, `references`, `priority` and
    `source_line` have no Markdown form; import derives all but `x-*` again. Only `US-<n>.<m>`
    story IDs and `E<n>` epic IDs can be written. A removed story is written as
    `— merged into <id>` when it has a `merged_into`, else as `— removed`.
    """
    _check_exportable(doc)
    out = [EXPORT_HEADING, ""]
    for epic in doc["epics"]:
        out += [f"### {epic['id']} {epic['name']}", ""]
        for s in doc["stories"]:
            if s["epic"] == epic["id"]:
                out += _markdown_lines(s)
        out.append("")
    return "\n".join(out)


def _check_exportable(doc: Mapping[str, Any]) -> None:
    bad = [e["id"] for e in doc["epics"] if not _LEGACY_EPIC_ID.match(e["id"])]
    bad += [s["id"] for s in doc["stories"] if not _LEGACY_STORY_ID.match(s["id"])]
    if bad:
        raise InputError(
            "Markdown export needs US-<n>.<m> story IDs and E<n> epic IDs; "
            f"{len(bad)} do not fit, e.g. {', '.join(bad[:3])}"
        )


def _markdown_lines(s: Mapping[str, Any]) -> list[str]:
    tag = f" [{s['tag']}]" if s.get("tag") else ""
    if _status(s) == "removed":
        reason = f": {s['removed_reason']}" if s.get("removed_reason") else "."
        fate = f"merged into {s['merged_into']}" if s["merged_into"] else "removed"
        return [f"- **{s['id']} {s['title']}{tag}** — {fate}{reason}"]
    head = f"- **{s['id']} {s['title']}{tag}.**"
    return [
        f"{head} {s['statement']}" if s["statement"] else head,
        *(f"  - {d}" for d in s.get("details", [])),
    ]
