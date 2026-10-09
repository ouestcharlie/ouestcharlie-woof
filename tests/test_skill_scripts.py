"""Tests for the skills' scripts — hierarchical tags written darktable-style."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

_SKILLS = Path(__file__).parent.parent / "plugins" / "woof-photo-workflows" / "skills"
_SCRIPTS = sorted(_SKILLS.glob("*/scripts/tag_files.py"))
_SHARED_SCRIPTS = ("tag_files.py", "sort_photos.py", "snapshot_sidecars.py")

_HEAD = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:lr="http://ns.adobe.com/lightroom/1.0/">'
)
_TAIL = "</rdf:Description></rdf:RDF></x:xmpmeta>"


def _bag(element: str, items: list[str]) -> str:
    lis = "".join(f"<rdf:li>{i}</rdf:li>" for i in items)
    return f"<{element}><rdf:Bag>{lis}</rdf:Bag></{element}>"


def _items(text: str, element: str) -> list[str] | None:
    blocks = re.findall(rf"<{element}\b.*?</{element}>", text, re.S)
    if not blocks:
        return None
    assert len(blocks) == 1, f"duplicate {element}"
    return re.findall(r"<rdf:li>(.*?)</rdf:li>", blocks[0])


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{path.stem}_{path.parts[-3]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=_SCRIPTS, ids=lambda p: p.parts[-3])
def tag_files(request: pytest.FixtureRequest) -> ModuleType:
    return _load(request.param)


@pytest.mark.parametrize("name", _SHARED_SCRIPTS)
def test_skill_scripts_are_identical(name: str) -> None:
    copies = sorted(_SKILLS.glob(f"*/scripts/{name}"))
    assert len(copies) == 2
    assert copies[0].read_text() == copies[1].read_text()


def test_darktable_sidecar_gets_paths_and_levels(tag_files: ModuleType) -> None:
    sidecar = (
        _HEAD
        + _bag("dc:subject", ["darktable", "format", "jpg"])
        + _bag("lr:hierarchicalSubject", ["darktable|format|jpg"])
        + _TAIL
    )
    new, note = tag_files.apply(sidecar, ["Trips | 2025 | Alps", "Family"])
    assert new is not None, note
    assert tag_files.valid(new)
    assert _items(new, "lr:hierarchicalSubject") == [
        "darktable|format|jpg",
        "Trips|2025|Alps",
        "Family",
    ]
    assert _items(new, "dc:subject") == [
        "darktable",
        "format",
        "jpg",
        "Trips",
        "2025",
        "Alps",
        "Family",
    ]
    # Idempotent: a second run changes nothing.
    assert tag_files.apply(new, ["Trips|2025|Alps", "Family"]) == (None, "tags already present")


def test_dc_subject_only_sidecar_gets_hierarchical_subject_with_existing_tags(
    tag_files: ModuleType,
) -> None:
    """darktable reads only lr:hierarchicalSubject once it exists: existing flat
    tags must be carried into it."""
    sidecar = _HEAD + _bag("dc:subject", ["Kids", "Family"]) + _TAIL
    new, note = tag_files.apply(sidecar, ["Places|Canada", "Kids"])
    assert new is not None, note
    assert "Places|Canada" in note
    assert _items(new, "lr:hierarchicalSubject") == ["Kids", "Family", "Places|Canada"]
    assert _items(new, "dc:subject") == ["Kids", "Family", "Places", "Canada"]


def test_flat_tags_already_present_completes_hierarchical_subject(
    tag_files: ModuleType,
) -> None:
    sidecar = _HEAD + _bag("dc:subject", ["Kids"]) + _TAIL
    new, note = tag_files.apply(sidecar, ["Kids"])
    assert new is not None
    assert note == "tags completed for darktable"
    assert _items(new, "lr:hierarchicalSubject") == ["Kids"]
    assert tag_files.apply(new, ["Kids"])[0] is None


def test_sidecar_without_tags_gets_both_elements(tag_files: ModuleType) -> None:
    sidecar = _HEAD + _TAIL
    new, _ = tag_files.apply(sidecar, ["A|B"])
    assert new is not None
    assert _items(new, "lr:hierarchicalSubject") == ["A|B"]
    assert _items(new, "dc:subject") == ["A", "B"]


def test_self_closing_description_expanded(tag_files: ModuleType) -> None:
    sidecar = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about=""/></rdf:RDF></x:xmpmeta>'
    )
    new, _ = tag_files.apply(sidecar, ["Family"])
    assert new is not None and tag_files.valid(new)
    assert _items(new, "lr:hierarchicalSubject") == ["Family"]


def test_empty_bag_is_filled(tag_files: ModuleType) -> None:
    sidecar = _HEAD + "<lr:hierarchicalSubject><rdf:Bag/></lr:hierarchicalSubject>" + _TAIL
    new, _ = tag_files.apply(sidecar, ["Family"])
    assert new is not None and tag_files.valid(new)
    assert _items(new, "lr:hierarchicalSubject") == ["Family"]


def test_element_without_bag_is_reported(tag_files: ModuleType) -> None:
    sidecar = _HEAD + "<lr:hierarchicalSubject><rdf:Seq/></lr:hierarchicalSubject>" + _TAIL
    assert tag_files.apply(sidecar, ["Family"]) == (
        None,
        "!! lr:hierarchicalSubject has no rdf:Bag",
    )


def test_escaped_tags_are_not_duplicated(tag_files: ModuleType) -> None:
    sidecar = _HEAD + _TAIL
    new, _ = tag_files.apply(sidecar, ["Tom & Jerry"])
    assert new is not None and tag_files.valid(new)
    assert tag_files.apply(new, ["Tom & Jerry"])[0] is None


def test_tag_present_in_another_case_is_not_added(tag_files: ModuleType) -> None:
    sidecar = (
        _HEAD + _bag("dc:subject", ["Paris"]) + _bag("lr:hierarchicalSubject", ["Paris"]) + _TAIL
    )
    assert tag_files.apply(sidecar, ["paris"]) == (None, "tags already present")
    assert tag_files.apply(sidecar, ["PARIS"]) == (None, "tags already present")


def test_new_path_reuses_existing_spelling_of_its_levels(tag_files: ModuleType) -> None:
    sidecar = (
        _HEAD
        + _bag("dc:subject", ["Places", "Europe"])
        + _bag("lr:hierarchicalSubject", ["Places|Europe"])
        + _TAIL
    )
    new, note = tag_files.apply(sidecar, ["places|europe|France"])
    assert new is not None, note
    assert _items(new, "lr:hierarchicalSubject") == ["Places|Europe", "Places|Europe|France"]
    assert _items(new, "dc:subject") == ["Places", "Europe", "France"]
    # A re-run in yet another case changes nothing.
    assert tag_files.apply(new, ["PLACES|Europe|france"]) == (None, "tags already present")


def test_requested_tags_differing_by_case_are_added_once(tag_files: ModuleType) -> None:
    new, _ = tag_files.apply(_HEAD + _TAIL, ["Paris", "paris", "A|b", "a|B|c"])
    assert new is not None
    assert _items(new, "lr:hierarchicalSubject") == ["Paris", "A|b", "A|b|c"]
    assert _items(new, "dc:subject") == ["Paris", "A", "b", "c"]


# ---------------------------------------------------------------------------
# sort_photos.py — enrich_sidecar
# ---------------------------------------------------------------------------


@pytest.fixture()
def sort_photos() -> ModuleType:
    return _load(_SKILLS / "sort-enrich-photos-strava" / "scripts" / "sort_photos.py")


def test_sort_photos_writes_paths_and_levels(sort_photos: ModuleType, tmp_path: Path) -> None:
    side = tmp_path / "a.jpg.xmp"
    side.write_text(_HEAD + _TAIL, encoding="utf-8")
    note = sort_photos.enrich_sidecar(
        str(side), {"desc": "Hike", "tags": ["Activities|Hiking", "Family"]}, dry=False
    )
    assert "Activities|Hiking" in note
    text = side.read_text(encoding="utf-8")
    assert _items(text, "lr:hierarchicalSubject") == ["Activities|Hiking", "Family"]
    assert _items(text, "dc:subject") == ["Activities", "Hiking", "Family"]


def test_sort_photos_dedupes_tags_regardless_of_case(
    sort_photos: ModuleType, tmp_path: Path
) -> None:
    side = tmp_path / "a.jpg.xmp"
    side.write_text(_HEAD + _TAIL, encoding="utf-8")
    sort_photos.enrich_sidecar(str(side), {"tags": ["Family", "family", "Kids|Family"]}, dry=False)
    text = side.read_text(encoding="utf-8")
    assert _items(text, "lr:hierarchicalSubject") == ["Family", "Kids|Family"]
    assert _items(text, "dc:subject") == ["Family", "Kids"]


def test_sort_photos_leaves_hierarchical_subject_alone(
    sort_photos: ModuleType, tmp_path: Path
) -> None:
    side = tmp_path / "a.jpg.xmp"
    original = _HEAD + _bag("lr:hierarchicalSubject", ["darktable|format|jpg"]) + _TAIL
    side.write_text(original, encoding="utf-8")
    note = sort_photos.enrich_sidecar(str(side), {"tags": ["Family"]}, dry=False)
    assert note == "already enriched, left alone"
    assert side.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# snapshot_sidecars.py — protected blocks
# ---------------------------------------------------------------------------


def test_snapshot_covers_hierarchical_subject() -> None:
    snapshot = _load(_SKILLS / "sort-enrich-photos-strava" / "scripts" / "snapshot_sidecars.py")
    text = (
        _HEAD
        + _bag("lr:hierarchicalSubject", ["A|B"])
        + _bag("dc:subject", ["A", "B"])
        + "<dc:description><rdf:Alt><rdf:li>x</rdf:li></rdf:Alt></dc:description>"
        + _TAIL
    )
    blocks = snapshot.META_RE.findall(text)
    assert [b.split(">", 1)[0] for b in blocks] == [
        "<lr:hierarchicalSubject",
        "<dc:subject",
        "<dc:description",
    ]
    assert snapshot.canonical(text) == snapshot.canonical(_HEAD + _TAIL)
