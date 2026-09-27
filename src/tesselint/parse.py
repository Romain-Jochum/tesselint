"""Snapshot the source and parse its stories: legacy Markdown §4 or stories v1 JSON (DESIGN §7, §9).

A Markdown source is cut to §4 (`snapshot.md`); a v1 JSON source is copied whole (`snapshot.json`,
docs/ui/DESIGN.md §4.5). Either way `snapshot.sha256` holds `<hex>  <snapshot name>`.
"""

import hashlib
import re
from collections.abc import Collection, Sequence
from pathlib import Path

from tesselint.models import ParseResult, Snapshot, Story, Tombstone

MD_SNAPSHOT = "snapshot.md"
JSON_SNAPSHOT = "snapshot.json"

STORY_ID = r"US-[0-9]+(?:\.[0-9]+)+"
# `### E7 Name`, `### E7: Name`, `### E7 — Name`: the epic ID and its name as written.
EPIC_HEADING = re.compile(r"^###\s+(E\d+)\b[\s:.—-]*(.*?)\s*$")
# `- **US-1.2 Title [tag]** — merged into US-1.1: reason` or `- **US-1.2 Title** — removed: reason`;
# groups: id, bold head, target (None when removed), reason.
TOMBSTONE = re.compile(
    rf"^-\s+\*\*({STORY_ID})\s+(.*?)\*\*\s*(?:—|--)\s*"
    rf"(?:merged into\s+({STORY_ID})|removed\b)(?::\s*(.*))?"
)
STORY_HEAD = re.compile(rf"^-\s+\*\*({STORY_ID})\s+(.*?)\*\*(.*)$")
SAMPLE_BACKLOG_URL = (
    "https://github.com/Romain-Jochum/tesselint/blob/main/tests/fixtures/sample_backlog.md"
)


def no_story_message(source: str) -> str:
    """The one-line error for a Markdown source in which no live story parses."""
    return (
        f"no story found in {source}: expected `### E<n> Name` epic headings over"
        f" `- **US-<n>.<m> Title.** …` story bullets, as in {SAMPLE_BACKLOG_URL};"
        " other formats: convert to stories v1 JSON (docs/input/INPUT.md)"
    )


def is_v1(path: Path) -> bool:
    """Whether a source is a stories v1 JSON document rather than Markdown (INPUT.md §4)."""
    return path.suffix == ".json"


def take_snapshot(source: Path, run_dir: Path) -> Snapshot:
    """Write the run's snapshot and snapshot.sha256 (DESIGN §9, docs/ui/DESIGN.md §4.5).

    A `.json` source is copied byte for byte to snapshot.json; a Markdown source is cut to its §4
    bytes (from `## 4.` up to `## 5.`) in snapshot.md.
    """
    raw = source.read_bytes()
    if is_v1(source):
        return _write_snapshot(run_dir, JSON_SNAPSHOT, raw)
    start_match = re.search(rb"^## 4\..*$", raw, re.MULTILINE)
    start_idx = start_match.start() if start_match else 0
    end_match = re.search(rb"^## 5\..*$", raw[start_idx:], re.MULTILINE)
    section_bytes = raw[start_idx : start_idx + end_match.start()] if end_match else raw[start_idx:]
    return _write_snapshot(run_dir, MD_SNAPSHOT, section_bytes)


def _write_snapshot(run_dir: Path, name: str, data: bytes) -> Snapshot:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / name
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (run_dir / "snapshot.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
    return Snapshot(path=path, text=data.decode("utf-8"), sha256=digest)


def load_snapshot(run_dir: Path) -> Snapshot:
    """Reload a run's snapshot.json, else its snapshot.md, checked against snapshot.sha256.

    DESIGN §2.7 (`--resume`), §9; docs/ui/DESIGN.md §4.5.
    """
    path = run_dir / JSON_SNAPSHOT
    if not path.exists():
        path = run_dir / MD_SNAPSHOT
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected_digest = (run_dir / "snapshot.sha256").read_text("utf-8").split()[0]
    if digest != expected_digest:
        raise ValueError(f"Snapshot hash mismatch: {digest} != {expected_digest}")
    return Snapshot(path=path, text=data.decode("utf-8"), sha256=digest)


def parse_snapshot(snapshot: Snapshot) -> ParseResult:
    """Parse a Markdown or v1 JSON snapshot into stories (docs/ui/DESIGN.md §4.5).

    A JSON snapshot is validated first; errors raise an InputError with one line per problem.
    """
    if not is_v1(snapshot.path):
        return parse_section(snapshot.text)
    # inputv1 builds on this module's parser, so it is imported here, not at the top.
    from tesselint import inputv1

    doc = inputv1.loads_v1(snapshot.text, str(snapshot.path))
    inputv1.check_v1(doc, str(snapshot.path))
    return inputv1.to_parse_result(doc)


def epic_names(snapshot: Snapshot) -> list[tuple[str, str]]:
    """(epic ID, heading name) pairs of a snapshot in narrative order (docs/ui/DESIGN.md §7.1)."""
    if is_v1(snapshot.path):
        from tesselint import inputv1  # see parse_snapshot

        doc = inputv1.loads_v1(snapshot.text, str(snapshot.path))
        return [(str(e["id"]), str(e["name"])) for e in doc["epics"]]
    return [
        (m[1], m[2])
        for line in snapshot.text.splitlines()
        if (m := EPIC_HEADING.match(line.strip()))
    ]


def split_head(bold: str) -> tuple[str, str]:
    """(title, tag) of a story's bold head (DESIGN §8).

    The first `[…]` is the tag; every bracket and the trailing periods leave the title.
    """
    tag_m = re.search(r"\[(.*?)\]", bold)
    title = re.sub(r"\s*\[.*?\]", "", bold).strip()
    return re.sub(r"\.+$", "", title).strip(), tag_m.group(1) if tag_m else ""


def parse_section(text: str) -> ParseResult:
    """Parse §4 text into stories, tombstones and unparsed `US-` lines (DESIGN §7, §9)."""
    stories: list[Story] = []
    tombstones: list[Tombstone] = []
    unparsed: list[tuple[int, str]] = []

    lines = text.splitlines()
    current_epic = ""
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        line_num = i + 1
        stripped = line.strip()

        epic_m = EPIC_HEADING.match(stripped)
        if epic_m:
            current_epic = epic_m.group(1)
            i += 1
            continue

        if stripped.startswith("- **US-"):
            tomb_m = TOMBSTONE.match(stripped)
            if tomb_m:
                t_id, t_title, t_target, _reason = tomb_m.groups()
                tombstones.append(
                    Tombstone(
                        id=t_id,
                        epic=current_epic,
                        title=t_title.strip(),
                        target=t_target.strip().rstrip(".") if t_target else None,
                        line=line_num,
                    )
                )
                i += 1
                continue

            story_m = STORY_HEAD.match(line)
            if not story_m:
                unparsed.append((line_num, line))
                i += 1
                continue

            s_id, inside_bold, rest = story_m.groups()
            title, tag = split_head(inside_bold)
            core = rest.strip()

            subs: list[str] = []
            i += 1
            while i < n:
                next_line = lines[i]
                next_stripped = next_line.strip()
                if (
                    next_stripped.startswith("- **US-")
                    or next_line.startswith("#")
                    or next_stripped.startswith("---")
                ):
                    break

                if not next_stripped:
                    i += 1
                    continue

                if next_stripped.startswith("|"):
                    cells = [c.strip() for c in next_stripped.strip("|").split("|")]
                    if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                        i += 1
                        continue
                    sub_text = "; ".join(c for c in cells if c)
                    subs.append(sub_text)
                    i += 1
                    continue

                bullet_m = re.match(r"^\s*-\s+(.*)$", next_line)
                if bullet_m:
                    subs.append(bullet_m.group(1).strip())
                    i += 1
                    continue

                subs.append(next_stripped)
                i += 1

            stories.append(
                Story(
                    id=s_id,
                    epic=current_epic,
                    title=title,
                    tag=tag,
                    core=core,
                    subs=tuple(subs),
                    line=line_num,
                )
            )
            continue

        i += 1

    return ParseResult(
        stories=tuple(stories), tombstones=tuple(tombstones), unparsed=tuple(unparsed)
    )


def select_epics(stories: Sequence[Story], epics: Collection[str] | None) -> tuple[Story, ...]:
    """Keep stories whose epic is in `epics` (None keeps all), in document order (DESIGN §4)."""
    if epics is None:
        return tuple(stories)

    epic_set = set(epics)
    selected: list[Story] = []
    for story in stories:
        id_epic = "E" + story.id.removeprefix("US-").split(".")[0]
        if story.epic in epic_set or id_epic in epic_set:
            selected.append(story)
    return tuple(selected)
