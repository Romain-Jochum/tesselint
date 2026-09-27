"""synth: a seeded synthetic §4 with planted duplicates, containments and bundles (DESIGN §6, §8).

The document goes through the real path: written to disk, cut by take_snapshot, parsed by
parse_section. Seeds are read from the packaged data/seeds.toml. A qualified tag is one with a
`;` qualifier, like `[MVP; the brief Lite]` in the real source.
"""

import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from tesselint.models import ParseResult, Story
from tesselint.parse import parse_section, take_snapshot
from tesselint.synth import Truth, generate


def _parse(text: str, tmp_path: Path) -> ParseResult:
    source = tmp_path / "stories.md"
    source.write_text(text, encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return parse_section(take_snapshot(source, run_dir).text)


def _id_epic(sid: str) -> str:
    """US-<n>.x -> E<n> (DESIGN §8, select_epics)."""
    return "E" + sid.removeprefix("US-").split(".")[0]


def _body(story: Story) -> str:
    return " ".join((story.core, *story.subs))


def _truth_ids(truth: Truth) -> set[str]:
    pairs = [*truth.duplicates, *truth.containments]
    return {sid for pair in pairs for sid in pair} | set(truth.bundled)


def _seeds() -> list[dict[str, Any]]:
    raw = (files("tesselint") / "data" / "seeds.toml").read_text(encoding="utf-8")
    seeds: list[dict[str, Any]] = tomllib.loads(raw)["seed"]
    return seeds


def test_same_seed_gives_identical_bytes_and_truth() -> None:
    """§6: same seed, same bytes; the Truth records the seed and n."""
    text_1, truth_1 = generate(seed=42, n=60)
    text_2, truth_2 = generate(seed=42, n=60)

    assert text_1.encode("utf-8") == text_2.encode("utf-8")
    assert truth_1 == truth_2
    assert (truth_1.seed, truth_1.n) == (42, 60)


def test_different_seed_gives_a_different_document() -> None:
    """§6: planting is driven by random.Random(seed), so another seed plants differently."""
    assert generate(seed=42)[0] != generate(seed=43)[0]


def test_default_size_plants_8_duplicates_8_containments_6_bundles() -> None:
    """§6 default size: 60 stories with 8 duplicates, 8 containments and 6 bundles."""
    _, truth = generate()

    assert truth.n == 60
    assert len(truth.duplicates) == 8
    assert len(truth.containments) == 8
    assert len(truth.bundled) == 6
    assert len(set(truth.bundled)) == 6


def test_document_is_framed_like_the_real_source() -> None:
    """§8 generate: the stories sit between `## 4. User stories` and `## 5. Workflows`."""
    text, _ = generate()

    lines = text.splitlines()
    assert "## 4. User stories" in lines
    assert "## 5. Workflows" in lines
    assert lines.index("## 4. User stories") < lines.index("## 5. Workflows")


@pytest.mark.parametrize("n", [60, 40])
def test_parses_to_n_live_stories_holding_every_truth_id(n: int, tmp_path: Path) -> None:
    """§6/§8: `n` counts live stories; the §4 parses cleanly and every planted ID is live."""
    text, truth = generate(seed=42, n=n)

    parsed = _parse(text, tmp_path)

    ids = [s.id for s in parsed.stories]
    assert len(ids) == n
    assert len(set(ids)) == n
    assert parsed.unparsed == ()
    assert truth.n == n
    assert _truth_ids(truth) <= set(ids)


def test_each_parser_variant_appears_once(tmp_path: Path) -> None:
    """§6 parser variants: one tombstone, one qualified tag, one empty core, one misplaced ID."""
    text, _ = generate()

    parsed = _parse(text, tmp_path)

    assert len(parsed.tombstones) == 1
    assert sum(";" in s.tag for s in parsed.stories) == 1
    assert sum(s.core == "" for s in parsed.stories) == 1
    assert sum(s.epic != _id_epic(s.id) for s in parsed.stories) == 1


def test_planted_duplicates_sit_in_different_epics() -> None:
    """§6 planting: a duplicate puts the paraphrase in another epic."""
    _, truth = generate()

    for first, second in truth.duplicates:
        assert _id_epic(first) != _id_epic(second), (first, second)


def test_containment_holds_the_contained_core_and_sub_bullets(tmp_path: Path) -> None:
    """§6 planting: a container is the seed's core + its addon + both sets of sub-bullets."""
    text, truth = generate()

    by_id = {s.id: s for s in _parse(text, tmp_path).stories}

    for container_id, contained_id in truth.containments:
        container, contained = by_id[container_id], by_id[contained_id]
        assert contained.core in _body(container), (container_id, contained_id)
        assert set(contained.subs) <= set(container.subs), (container_id, contained_id)
        assert len(_body(container)) > len(_body(contained))


def test_bundle_fuses_seeds_that_no_longer_stand_alone(tmp_path: Path) -> None:
    """§6 planting: a bundle fuses 2-3 seeds from different epics and removes the originals."""
    text, truth = generate()
    stories = _parse(text, tmp_path).stories
    seeds = _seeds()

    for bundle_id in truth.bundled:
        bundle = next(s for s in stories if s.id == bundle_id)
        parts = [seed for seed in seeds if seed["core"] in _body(bundle)]
        assert 2 <= len(parts) <= 3, bundle_id
        assert len({seed["epic"] for seed in parts}) == len(parts), bundle_id
        others = [_body(s) for s in stories if s.id != bundle_id]
        for seed in parts:
            cores = [seed["core"], seed.get("paraphrase", {}).get("core", seed["core"])]
            assert not any(core in body for core in cores for body in others), seed["id"]
