"""Export accepted decisions as JSON and Markdown (docs/ui/DESIGN.md §2 Export, §7.4).

The JSON follows `schemas/decisions-export.v1.schema.json`. Items are accepted decisions whose
stories the run still holds: current ones always, stale ones only with `include_stale`; missing
ones never (a gone story cannot be checked against the source). Story texts, titles and hashes
come from the run, so the session that edits the source can check each item before applying it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from tesselint.server.payloads import DecisionView, RunBundle, StoryRow

if TYPE_CHECKING:
    from tesselint.derive import Derived
    from tesselint.proposals import Proposal

EXPORT_FORMAT = "tesselint/decisions-export"
EXPORT_VERSION = 1
EXPORTS_DIR = "exports"  # under <state>/ (§3 Location)

ExportAction = Literal["merge", "split", "untangle"]

EXPORT_INTRO = "Apply in order; if a story's text no longer matches, skip it and report its ID."
DO_LINES: Mapping[ExportAction, str] = MappingProxyType(
    {
        "merge": (
            "Fold {drop}'s distinct details into {keep}, then replace its line with"
            " `- **{drop} {drop_title}** — merged into {keep}`"
        ),
        "split": "Keep {story} for one part; give the others new, unused IDs",
        "untangle": ("Make each boundary explicit; merge nothing unless a merge item says so"),
    }
)

ACTION_OF: Mapping[str, ExportAction] = MappingProxyType(
    {"merge": "merge", "split": "split", "overlap": "untangle"}
)
_RANK = MappingProxyType({"merge": 0, "split": 1, "overlap": 2})  # merges, splits, overlaps (§2)
# Evidence fields each kind takes from the run's proposal details (§7.3), and the one field that
# equals the decisive value when the run no longer proposes the decision (proposals.py).
_DETAIL_FIELDS = MappingProxyType(
    {"merge": ("relation", "m", "o"), "split": ("bundled",), "overlap": ("mean_o",)}
)
_DECISIVE_ALIAS = MappingProxyType({"merge": "m", "split": "bundled", "overlap": "mean_o"})
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._~-]")


def build_export(
    views: Sequence[DecisionView],
    run: RunBundle,
    *,
    include_stale: bool,
    derived: Derived | None = None,
) -> dict[str, Any]:
    """Accepted merges (by kept story), then splits, then overlaps, with warnings (§2, §7.4).

    `derived` (additive, optional) is the same run's derivation: with it, each item's evidence
    adds the proposal's borderline flag, relation, m, o and evidence lines when the run proposes
    it. `evidence.decisive_value` is the stored one (PLAN resolution 2).
    """
    rows = {row["id"]: row for row in run["stories"]}
    proposals = {p.key: p for p in derived.proposals} if derived is not None else {}
    picked = [v for v in views if _exportable(v, rows, include_stale=include_stale)]
    picked.sort(key=lambda v: _sort_key(v, rows))
    warnings = _warnings(picked)
    items = [
        _item(order, view, rows, proposals.get(view["key"]), warnings[view["key"]])
        for order, view in enumerate(picked, start=1)
    ]
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "run": {"ref": run["run"]["ref"], "snapshot_sha256": run["run"]["snapshot_sha256"]},
        "items": items,
    }


def export_markdown_doc(doc: Mapping[str, Any]) -> str:
    """Render an export document as the §7.4 Markdown: header, intro, one section per item."""
    items: Sequence[Mapping[str, Any]] = doc["items"]
    lines = [
        "# tesselint decisions to apply",
        "",
        f"- Run: `{doc['run']['ref']}`",
        f"- Snapshot: `{doc['run']['snapshot_sha256']}`",
        f"- Items: {len(items)}",
        "",
        EXPORT_INTRO,
    ]
    if not items:
        lines += ["", "No accepted decisions to apply."]
    for item in items:
        lines += ["", *_markdown_item(item)]
    return "\n".join(lines) + "\n"


def export_stem(doc: Mapping[str, Any], now: datetime | None = None) -> str:
    """A file name without extension for an export: `decisions-<ref>-<UTC time>`."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return _UNSAFE_NAME.sub("_", f"decisions-{doc['run']['ref']}-{stamp}")


def write_export(doc: Mapping[str, Any], state_dir: Path) -> tuple[Path, Path]:
    """Write `<state>/exports/<stem>.md` and `.json` (never overwriting); return both paths."""
    exports = state_dir / EXPORTS_DIR
    exports.mkdir(parents=True, exist_ok=True)
    stem = export_stem(doc)
    md_text = export_markdown_doc(doc)
    json_text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    for n in count(1):
        name = stem if n == 1 else f"{stem}-{n}"
        md_path, json_path = exports / f"{name}.md", exports / f"{name}.json"
        try:
            with json_path.open("x", encoding="utf-8") as fh:
                fh.write(json_text)
        except FileExistsError:
            continue
        with md_path.open("w", encoding="utf-8") as fh:
            fh.write(md_text)
        return md_path, json_path
    raise AssertionError("unreachable")  # pragma: no cover


# --- items ----------------------------------------------------------------------------------------


def _exportable(view: DecisionView, rows: Mapping[str, StoryRow], *, include_stale: bool) -> bool:
    """Accepted, current (or stale on request), and every story still in the run."""
    wanted = view["status"] == "current" or (include_stale and view["status"] == "stale")
    return view["action"] == "accept" and wanted and all(sid in rows for sid in view["stories"])


def _sort_key(view: DecisionView, rows: Mapping[str, StoryRow]) -> tuple[int, int, int, str]:
    """Merges by kept then dropped story, splits by story, overlaps by first member (doc order)."""
    places = [rows[sid]["order"] for sid in view["stories"]]
    if view["kind"] == "overlap":
        places = sorted(places)
    first, second = places[0], places[1] if len(places) > 1 else 0
    return (_RANK[view["kind"]], first, second, view["key"])


def _item(
    order: int,
    view: DecisionView,
    rows: Mapping[str, StoryRow],
    proposal: Proposal | None,
    warnings: list[str],
) -> dict[str, Any]:
    action = ACTION_OF[view["kind"]]
    ids = view["stories"]
    if action == "merge":
        keep, drop = ids
        instruction = DO_LINES["merge"].format(drop=drop, drop_title=rows[drop]["title"], keep=keep)
    elif action == "split":
        instruction = DO_LINES["split"].format(story=ids[0])
    else:
        instruction = DO_LINES["untangle"]
    return {
        "order": order,
        "key": view["key"],
        "action": action,
        "stories": [
            {
                "id": sid,
                "title": rows[sid]["title"],
                "text_hash": rows[sid]["text_hash"],
                "text": rows[sid]["text"],
            }
            for sid in ids
        ],
        "note": view["note"],
        "evidence": _evidence(view, proposal),
        "status": view["status"],
        "warnings": warnings,
        "instruction": instruction,
    }


def _evidence(view: DecisionView, proposal: Proposal | None) -> dict[str, Any]:
    """The stored decisive value, plus what the run's proposal says (or the value's alias)."""
    kind = view["kind"]
    stored = view.get("decisive_value")
    value = stored if isinstance(stored, int | float) else None
    if value is None and proposal is not None:
        value = proposal.decisive_value
    if value is None:
        value = 0.0
    evidence: dict[str, Any] = {"decisive_value": value}
    if proposal is None:
        evidence[_DECISIVE_ALIAS[kind]] = value
        return evidence
    evidence["borderline"] = proposal.borderline
    details = proposal.details or {}
    for name in _DETAIL_FIELDS[kind]:
        if details.get(name) is not None:
            evidence[name] = details[name]
    if proposal.evidence:
        evidence["lines"] = list(proposal.evidence)
    return evidence


def _warnings(views: Sequence[DecisionView]) -> dict[str, list[str]]:
    """Per item: stale texts, applied merges, a merge plus a split on a story, chains (§2)."""
    found: dict[str, list[str]] = {v["key"]: [] for v in views}
    merges = [v for v in views if v["kind"] == "merge"]
    splits = {v["stories"][0]: v for v in views if v["kind"] == "split"}
    for view in views:
        if view["status"] == "stale":
            changed = ", ".join(view["changed"]) or "a story"
            found[view["key"]].append(
                f"{changed} changed since this was decided; compare the texts before applying"
            )
    for merge in merges:
        keep, drop = merge["stories"]
        if merge.get("applied"):
            found[merge["key"]].append(f"The source already marks {drop} as merged into {keep}")
        for sid in (keep, drop):
            split = splits.get(sid)
            if split is not None:
                found[merge["key"]].append(
                    f"{sid} also has an accepted split ({split['key']}); apply one of the two,"
                    " then check the other still makes sense"
                )
                found[split["key"]].append(
                    f"{sid} is also in an accepted merge ({merge['key']}); apply one of the two,"
                    " then check the other still makes sense"
                )
    _chain_warnings(merges, found)
    return {key: list(dict.fromkeys(texts)) for key, texts in found.items()}


def _chain_warnings(merges: Sequence[DecisionView], found: dict[str, list[str]]) -> None:
    """X into Y plus Y into Z suggests X into Z; two merges of one story; merge cycles."""
    by_drop: dict[str, list[DecisionView]] = {}
    for merge in merges:
        by_drop.setdefault(merge["stories"][1], []).append(merge)
    for drop, group in by_drop.items():
        if len(group) > 1:
            keeps = " and ".join(m["stories"][0] for m in group)
            for merge in group:
                found[merge["key"]].append(f"{drop} is merged into both {keeps}; keep one merge")
    for first in merges:
        x, y = first["stories"][1], first["stories"][0]  # x merges into y
        for second in by_drop.get(y, []):
            z = second["stories"][0]  # y merges into z
            if z == x:
                text = f"{x} and {y} merge into each other; keep one direction"
            else:
                text = (
                    f"Chain: {x} merges into {y}, which merges into {z}; fold {x} into {z} as well"
                )
            found[first["key"]].append(text)
            found[second["key"]].append(text)


# --- Markdown -------------------------------------------------------------------------------------


def _markdown_item(item: Mapping[str, Any]) -> list[str]:
    lines = [
        f"## {item['order']}. {str(item['action']).capitalize()}",
        "",
        f"Key `{item['key']}` · {item['status']}",
        "",
    ]
    for story in item["stories"]:
        lines.append(f"- **{story['id']}** {story['title']} · text_hash `{story['text_hash']}`")
        lines += ["", _quote(story["text"], indent="  "), ""]
    lines.append(f"Why: {_why(item)}")
    lines += [f"- {line}" for line in item["evidence"].get("lines", [])]
    if item["note"]:
        lines += ["", "Note:", "", _quote(item["note"])]
    if item["warnings"]:
        lines += ["", "Warnings:", ""]
        lines += [f"- {warning}" for warning in item["warnings"]]
    lines += ["", f"**Do**: {item['instruction']}"]
    return lines


def _why(item: Mapping[str, Any]) -> str:
    """`m 0.91 · o 0.97 · duplicate · borderline`, from the item's evidence."""
    evidence: Mapping[str, Any] = item["evidence"]
    parts = []
    if "relation" in evidence:
        parts.append(str(evidence["relation"]))
    for name, label in (("m", "m"), ("o", "o"), ("bundled", "bundled"), ("mean_o", "mean o")):
        if name in evidence:
            parts.append(f"{label} {evidence[name]:.2f}")
    if not parts and "decisive_value" in evidence:
        parts.append(f"decisive value {evidence['decisive_value']:.2f}")
    if evidence.get("borderline"):
        parts.append("borderline")
    return " · ".join(parts) or "no evidence recorded"


def _quote(text: str, indent: str = "") -> str:
    """`text` as a Markdown blockquote, so no line of it can start a new section."""
    return "\n".join(f"{indent}> {line}".rstrip() for line in text.splitlines() or [""])
