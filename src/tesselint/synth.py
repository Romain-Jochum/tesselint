"""Synthetic stories with planted duplicates, containments and bundles (DESIGN §6)."""

import hashlib
import random
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from tesselint.errors import UsageError
from tesselint.inputv1 import SYNTHETIC_KEY, convert_markdown

GENERATOR = "tesselint-synth/1"
CONVERTER = "tesselint synth/1"  # source.converter of a v1 synth document (docs/ui/DESIGN.md §4.2)
DEFAULT_PLANTS = (8, 8, 6)  # duplicates, containments, bundles from n = 60 up (§6)

Seed = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Truth:
    """What was planted in a synthetic §4; containments are (container, contained).

    `bundle_parts` lists the seed ids fused into each `bundled` story, in the same order.
    """

    seed: int
    n: int
    duplicates: tuple[tuple[str, str], ...]
    containments: tuple[tuple[str, str], ...]
    bundled: tuple[str, ...]
    bundle_parts: tuple[tuple[str, ...], ...] = ()


def truth_to_dict(truth: Truth) -> dict[str, Any]:
    """The truth.json body of DESIGN §6, bundles with their seed parts."""
    parts = truth.bundle_parts or tuple(() for _ in truth.bundled)
    return {
        "generator": GENERATOR,
        "seed": truth.seed,
        "n": truth.n,
        "seeds_sha256": hashlib.sha256(_seeds_bytes()).hexdigest(),
        "duplicates": [list(pair) for pair in truth.duplicates],
        "containments": [{"container": c, "contained": d} for c, d in truth.containments],
        "bundled": [
            {"id": sid, "parts": list(p)} for sid, p in zip(truth.bundled, parts, strict=True)
        ],
    }


def generate(seed: int = 42, n: int = 60) -> tuple[str, Truth]:
    """Return a §4-shaped markdown with n live stories from data/seeds.toml and its Truth (§6).

    Plants scale down below n = 60 (and drop to what fits below n = 5). An n the seed set cannot
    fill raises UsageError.
    """
    if n < 1:
        raise UsageError(f"synth needs n >= 1, got {n}")
    rng = random.Random(seed)
    data: dict[str, Any] = tomllib.loads(_seeds_bytes().decode("utf-8"))
    seeds: list[Seed] = data["seed"]
    n_dup, n_cont, n_bundle = _plant_counts(n)

    bundles = _pick_bundles(seeds, n_bundle, rng)
    used = {s["id"] for parts in bundles for s in parts}
    dup_seeds = _pick(seeds, "paraphrase", n_dup, used, rng)
    used |= {s["id"] for s in dup_seeds}
    cont_seeds = _pick(seeds, "addon", n_cont, used, rng)
    used |= {s["id"] for s in cont_seeds}
    normal_pool = _shuffled([s for s in seeds if s["id"] not in used], rng)
    addon_pool = [s for s in normal_pool if "addon" in s]  # an addon alone is one more story

    planted = len(bundles) + 2 * len(dup_seeds) + 2 * len(cont_seeds)
    capacity = planted + len(normal_pool) + len(addon_pool)
    if n > capacity:
        raise UsageError(f"synth can build at most {capacity} stories with seed {seed}, got n={n}")
    normal_seeds = normal_pool[: n - planted]
    addon_seeds = addon_pool[: n - planted - len(normal_seeds)]

    items: list[dict[str, Any]] = []
    for i, s in enumerate(dup_seeds):  # the paraphrase goes to another epic
        items.append(_item(s, "dup_orig", i))
        items.append(_item(s["paraphrase"], "dup_para", i, epic=_next_epic(s), tag=s["tag"]))
    for i, s in enumerate(cont_seeds):  # container = seed + addon, titled like the addon
        add = s["addon"]
        container = {
            "title": add["title"],
            "core": f"{s['core']} {add['core']}",
            "subs": [*s["subs"], *add["subs"]],
        }
        items.append(_item(s, "cont_contained", i))
        items.append(_item(container, "cont_container", i, epic=_next_epic(s), tag=s["tag"]))
    for i, parts in enumerate(bundles):
        fused = {
            "title": _join_titles([p["title"].rstrip(".") for p in parts]),
            "core": " ".join(p["core"] for p in parts),
            "subs": [sub for p in parts for sub in p["subs"]],
        }
        items.append(_item(fused, "bundle", i, epic=_epic(parts[0]), tag=parts[0]["tag"]))
    items += [_item(s, "normal", 0) for s in normal_seeds]
    items += [
        _item(_alone(s["addon"]), "normal", 0, epic=(_epic(s) + 2) % 8 + 1, tag=s["tag"])
        for s in addon_seeds
    ]

    normals = [item for item in items if item["role"] == "normal"]
    if len(normals) >= 3:  # parser variants
        normals[0]["tag"] = "MVP; the brief Lite"
        normals[1]["core"] = ""
        normals[2]["misplaced"] = True

    by_epic: dict[int, list[dict[str, Any]]] = {e: [] for e in range(1, 9)}
    for item in items:
        by_epic[item["epic"]].append(item)
    ids: dict[tuple[str, int], str] = {}
    for e, epic_items in by_epic.items():
        for idx, item in enumerate(epic_items, start=1):
            item["id"] = f"US-{e % 8 + 1}.99" if item.get("misplaced") else f"US-{e}.{idx}"
            ids[(item["role"], item["idx"])] = item["id"]

    truth = Truth(
        seed=seed,
        n=n,
        duplicates=tuple(
            (ids[("dup_orig", i)], ids[("dup_para", i)]) for i in range(len(dup_seeds))
        ),
        containments=tuple(
            (ids[("cont_container", i)], ids[("cont_contained", i)]) for i in range(len(cont_seeds))
        ),
        bundled=tuple(ids[("bundle", i)] for i in range(len(bundles))),
        bundle_parts=tuple(tuple(p["id"] for p in parts) for parts in bundles),
    )
    return _document(data["epics"], by_epic), truth


def generate_v1(seed: int = 42, n: int = 60) -> tuple[dict[str, Any], Truth]:
    """The stories of `generate(seed, n)` as a stories v1 document, and the same Truth.

    The document is the markdown's §4 converted word for word (so every story renders to the same
    Jev text) and marked `"x-synthetic": true` (docs/ui/DESIGN.md §4.2, §4.5).
    """
    document, truth = generate(seed=seed, n=n)
    doc = convert_markdown(
        document.encode("utf-8"), path=f"synth:seed={seed},n={n}", converter=CONVERTER
    )
    return {**doc, SYNTHETIC_KEY: True}, truth


def _seeds_bytes() -> bytes:
    return (files("tesselint") / "data" / "seeds.toml").read_bytes()


def _plant_counts(n: int) -> tuple[int, int, int]:
    """(duplicates, containments, bundles) for n live stories; §11: n = 20 plants each kind."""
    if n >= 60:
        return DEFAULT_PLANTS
    dup = cont = max(1, 8 * n // 60)
    bundle = max(1, 6 * n // 60)
    while 2 * dup + 2 * cont + bundle > n:  # below n = 5 not every kind fits
        if cont:
            cont -= 1
        elif dup:
            dup -= 1
        else:
            bundle -= 1
    return dup, cont, bundle


def _shuffled(seeds: list[Seed], rng: random.Random) -> list[Seed]:
    ordered = sorted(seeds, key=lambda s: str(s["id"]))
    rng.shuffle(ordered)
    return ordered


def _pick(
    seeds: list[Seed], key: str, count: int, used: set[str], rng: random.Random
) -> list[Seed]:
    return _shuffled([s for s in seeds if key in s and s["id"] not in used], rng)[:count]


def _pick_bundles(seeds: list[Seed], count: int, rng: random.Random) -> list[list[Seed]]:
    """Fuse 2 seeds from different epics per bundle, 3 for every third bundle (§6).

    Seeds without a paraphrase or addon go first, then addon-only seeds; paraphrase seeds are
    kept for duplicates.
    """
    plain = [s for s in seeds if "paraphrase" not in s and "addon" not in s]
    addon_only = [s for s in seeds if "paraphrase" not in s and "addon" in s]
    avail = _shuffled(plain, rng) + _shuffled(addon_only, rng)
    bundles: list[list[Seed]] = []
    for i in range(count):
        size = 3 if i % 3 == 2 else 2
        parts: list[Seed] = []
        for s in avail:
            if len(parts) < size and all(s["epic"] != p["epic"] for p in parts):
                parts.append(s)
        if len(parts) < 2:
            break
        avail = [s for s in avail if s not in parts]
        bundles.append(parts)
    return bundles


def _epic(s: Seed) -> int:
    return int(str(s["epic"]).removeprefix("E"))


def _next_epic(s: Seed) -> int:
    return _epic(s) % 8 + 1


def _item(
    src: Seed, role: str, idx: int, *, epic: int | None = None, tag: str | None = None
) -> dict[str, Any]:
    return {
        "epic": _epic(src) if epic is None else epic,
        "title": src["title"],
        "tag": src["tag"] if tag is None else tag,
        "core": src["core"],
        "subs": list(src["subs"]),
        "role": role,
        "idx": idx,
    }


def _alone(addon: Seed) -> Seed:
    """An addon as a filler story of its own: its own title, and "I also" read as "I".

    The container title and "I also" both name the base seed, so a filler wearing them reads as
    containing that seed: a merge nobody planted. Containers keep both.
    """
    core = str(addon["core"])
    if core.startswith("I also "):
        core = "I " + core.removeprefix("I also ")
    return {**addon, "title": addon.get("alone_title", addon["title"]), "core": core}


def _join_titles(titles: list[str]) -> str:
    return ", ".join(titles[:-1]) + " and " + titles[-1]


def _document(epic_names: dict[str, str], by_epic: dict[int, list[dict[str, Any]]]) -> str:
    lines = ["# Plotline Allotments", "", "## 4. User stories", ""]
    for e in range(1, 9):
        lines.append(f"### E{e}: {epic_names[f'E{e}']}")
        if e == 1:
            lines.append("- **US-1.90 [MVP] Former allocation rules** — merged into US-1.1")
        for item in by_epic[e]:
            title = item["title"].rstrip(".")
            tag = f" [{item['tag']}]" if item["tag"] else ""
            head = f"- **{item['id']}{tag} {title}.**"
            lines.append(f"{head} {item['core']}" if item["core"] else head)
            lines += [f"  - {sub}" for sub in item["subs"]]
        lines.append("")
    lines += ["## 5. Workflows", "", "Workflows go here."]
    return "\n".join(lines)
