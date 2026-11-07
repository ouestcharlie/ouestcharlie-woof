"""Tests for the skills' scripts — hierarchical tags written darktable-style."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ouestcharlie_toolkit.media import PHOTO_SUFFIXES, VIDEO_SUFFIXES
from ouestcharlie_toolkit.xmp.parse import parse_xmp

_SKILLS = Path(__file__).parent.parent / "plugins" / "woof-photo-workflows" / "skills"
_SCRIPTS = sorted(_SKILLS.glob("*/scripts/tag_files.py"))
_SHARED_SCRIPTS = (
    "tag_files.py",
    "retag_files.py",
    "sort_photos.py",
    "verify_sort.py",
    "snapshot_sidecars.py",
    "media_ext.py",
)

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
    """Load a script as `python scripts/<name>.py` would: its folder is importable,
    so `from media_ext import ...` resolves to the skill's own copy."""
    spec = importlib.util.spec_from_file_location(f"{path.stem}_{path.parts[-3]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
        sys.modules.pop("media_ext", None)  # the other skill must load its own copy
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
# retag_files.py — replace one tag path
# ---------------------------------------------------------------------------


@pytest.fixture(params=sorted(_SKILLS.glob("*/scripts/retag_files.py")), ids=lambda p: p.parts[-3])
def retag_files(request: pytest.FixtureRequest) -> ModuleType:
    module = _load(request.param)
    sys.modules.pop("tag_files", None)  # imported by retag_files from its own folder
    return module


def _sidecar(hierarchical: list[str] | None, subject: list[str]) -> str:
    h = "" if hierarchical is None else _bag("lr:hierarchicalSubject", hierarchical)
    return _HEAD + h + _bag("dc:subject", subject) + _TAIL


def test_retag_removes_old_levels_from_dc_subject(retag_files: ModuleType) -> None:
    sidecar = _sidecar(["Places|France|Paris", "Family"], ["Places", "France", "Paris", "Family"])
    new, note = retag_files.retag(sidecar, "Places|France|Paris", "Places|France|Lyon")
    assert note == "hierarchical tag replaced"
    assert _items(new, "lr:hierarchicalSubject") == ["Family", "Places|France|Lyon"]
    assert _items(new, "dc:subject") == ["Places", "France", "Family", "Lyon"]
    # the indexer reads a dc:subject term outside every path as a flat tag
    assert sorted(parse_xmp(new).tags) == ["Family", "Places|France|Lyon"]


def test_retag_keeps_levels_still_used_by_other_paths(retag_files: ModuleType) -> None:
    sidecar = _sidecar(["Sport|Ski|Touring", "Sport|Ski|Race"], ["Sport", "Ski", "Touring", "Race"])
    new, _note = retag_files.retag(sidecar, "Sport|Ski|Race", "Sport|Race")
    assert _items(new, "lr:hierarchicalSubject") == ["Sport|Ski|Touring", "Sport|Race"]
    assert _items(new, "dc:subject") == ["Sport", "Ski", "Touring", "Race"]


def test_retag_ignores_old_tag_that_is_only_a_level(retag_files: ModuleType) -> None:
    sidecar = _sidecar(["Sport|Ski"], ["Sport", "Ski"])
    assert retag_files.retag(sidecar, "Ski", "Skiing") == (None, "old tag not present")


def test_retag_migrates_flat_dc_subject_tag(retag_files: ModuleType) -> None:
    sidecar = _sidecar(["Sport|Ski"], ["Sport", "Ski", "Kayak"])
    new, note = retag_files.retag(sidecar, "kayak", "Sport|Kayak")
    assert note == "legacy dc:subject tag migrated"
    assert _items(new, "lr:hierarchicalSubject") == ["Sport|Ski", "Sport|Kayak"]
    assert _items(new, "dc:subject") == ["Sport", "Ski", "Kayak"]


def test_retag_matches_stored_path_with_spaces(retag_files: ModuleType) -> None:
    sidecar = _sidecar(["Sport | Ski"], ["Sport", "Ski"])
    new, note = retag_files.retag(sidecar, "Sport|Ski", "Activities|Ski")
    assert note == "hierarchical tag replaced"
    assert _items(new, "lr:hierarchicalSubject") == ["Activities|Ski"]
    assert _items(new, "dc:subject") == ["Ski", "Activities"]


def test_retag_reuses_existing_spelling(retag_files: ModuleType) -> None:
    sidecar = _sidecar(
        ["Sport|Ski|Touring", "BackcountrySki"], ["Sport", "Ski", "Touring", "BackcountrySki"]
    )
    new, _note = retag_files.retag(sidecar, "BackcountrySki", "sport|ski|backcountry")
    assert _items(new, "lr:hierarchicalSubject") == [
        "Sport|Ski|Touring",
        "Sport|Ski|backcountry",
    ]
    assert _items(new, "dc:subject") == ["Sport", "Ski", "Touring", "backcountry"]


def test_retag_dc_subject_only_sidecar_gets_hierarchical_subject(
    retag_files: ModuleType,
) -> None:
    sidecar = _sidecar(None, ["BackcountrySki", "Family"])
    new, note = retag_files.retag(sidecar, "BackcountrySki", "Sport|Ski")
    assert note == "hierarchical tag added"
    assert _items(new, "lr:hierarchicalSubject") == ["Sport|Ski", "Family"]
    assert _items(new, "dc:subject") == ["Family", "Sport", "Ski"]


def test_retag_is_idempotent(retag_files: ModuleType) -> None:
    sidecar = _sidecar(["BackcountrySki"], ["BackcountrySki"])
    new, _note = retag_files.retag(sidecar, "BackcountrySki", "Sport|Ski")
    assert retag_files.retag(new, "BackcountrySki", "Sport|Ski") == (
        None,
        "old tag not present",
    )


def _library(tmp_path: Path) -> Path:
    """Three photos tagged BackcountrySki, in 2024/Alps, 2024/Alps/Day2 and 2025."""
    for folder in ("2024/Alps", "2024/Alps/Day2", "2025"):
        directory = tmp_path / folder
        directory.mkdir(parents=True)
        (directory / "a.jpg").write_bytes(b"")
        (directory / "a.jpg.xmp").write_text(
            _sidecar(["BackcountrySki"], ["BackcountrySki"]), encoding="utf-8"
        )
    return tmp_path


def _changed(found: Any, lib: Path) -> list[str]:
    """Sidecars a scan would change, relative to the library."""
    return sorted(Path(todo[0]).relative_to(lib).as_posix() for todo in found.todo)


def test_retag_scan_is_limited_to_folders_and_their_subfolders(
    retag_files: ModuleType, tmp_path: Path
) -> None:
    lib = _library(tmp_path)
    found = retag_files.scan([str(lib / "2024" / "Alps")], "BackcountrySki", "Sport|Ski")
    assert _changed(found, lib) == ["2024/Alps/Day2/a.jpg.xmp", "2024/Alps/a.jpg.xmp"]


def test_retag_scan_overlapping_folders_counted_once(
    retag_files: ModuleType, tmp_path: Path
) -> None:
    lib = _library(tmp_path)
    found = retag_files.scan(
        [str(lib / "2024"), str(lib / "2024" / "Alps" / "Day2"), str(lib / "2025")],
        "BackcountrySki",
        "Sport|Ski",
    )
    assert len(found.todo) == 3


def test_retag_main_writes_only_inside_folder(
    retag_files: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = _library(tmp_path)
    argv = ["retag_files.py", "--lib", str(lib), "--folder", "2025"]
    argv += ["--old-tag", "BackcountrySki", "--new-tag", "Sport|Ski", "--go"]
    monkeypatch.setattr(sys, "argv", argv)
    retag_files.main()
    retagged = (lib / "2025" / "a.jpg.xmp").read_text(encoding="utf-8")
    assert _items(retagged, "lr:hierarchicalSubject") == ["Sport|Ski"]
    untouched = (lib / "2024" / "Alps" / "a.jpg.xmp").read_text(encoding="utf-8")
    assert _items(untouched, "lr:hierarchicalSubject") == ["BackcountrySki"]
    assert len(list((lib / "_sorting").glob("xmp_backup_retagging_*.tar.gz"))) == 1


def test_retag_main_rejects_missing_folder(
    retag_files: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = _library(tmp_path)
    argv = ["retag_files.py", "--lib", str(lib), "--folder", "2026"]
    argv += ["--old-tag", "BackcountrySki", "--new-tag", "Sport|Ski"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        retag_files.main()


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


@pytest.mark.parametrize(
    "path", sorted(_SKILLS.glob("*/scripts/media_ext.py")), ids=lambda p: p.parts[-3]
)
def test_media_extensions_match_toolkit(path: Path) -> None:
    """The scripts run standalone, so each skill keeps its own copy of the media list."""
    module = _load(path)
    assert len(module.MEDIA_EXT) == len(set(module.MEDIA_EXT))
    assert set(module.MEDIA_EXT) == PHOTO_SUFFIXES | VIDEO_SUFFIXES


@pytest.mark.parametrize(
    "path",
    [p for p in sorted(_SKILLS.glob("*/scripts/*.py")) if "from media_ext import" in p.read_text()],
    ids=lambda p: f"{p.parts[-3]}/{p.name}",
)
def test_scripts_use_the_skill_media_list(path: Path) -> None:
    assert set(_load(path).MEDIA_EXT) == PHOTO_SUFFIXES | VIDEO_SUFFIXES
