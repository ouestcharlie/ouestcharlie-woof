"""Integration test: hierarchical tags from a darktable sidecar, end to end.

A real Whitebeard indexes a library holding a darktable-written sidecar
(``lr:hierarchicalSubject`` + ``dc:subject``, with darktable's automatic
``darktable|…`` tags), then a real Wally searches and summarizes it — both
spawned through ``AgentClient``, with the library's ``excluded_tag_prefixes``
travelling in ``WOOF_BACKEND_CONFIG`` as in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from woof.agent_client import AgentClient
from woof.config import LibraryConfig

# Minimal valid JPEG (SOI + JFIF APP0 + EOI), no EXIF: the tags come from the sidecar.
_MINIMAL_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

# Sidecar as darktable writes it: every path in lr:hierarchicalSubject, every
# level in dc:subject, plus darktable's own automatic tags.
_DARKTABLE_SIDECAR = """\
<x:xmpmeta xmlns:x='adobe:ns:meta/'>
 <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
  <rdf:Description rdf:about=''
    xmlns:dc='http://purl.org/dc/elements/1.1/'
    xmlns:lr='http://ns.adobe.com/lightroom/1.0/'
    xmlns:darktable='http://darktable.sf.net/'>
   <dc:subject><rdf:Bag>
    <rdf:li>darktable</rdf:li><rdf:li>format</rdf:li><rdf:li>jpg</rdf:li>
    <rdf:li>Places</rdf:li><rdf:li>Europe</rdf:li><rdf:li>France</rdf:li>
    <rdf:li>Paris</rdf:li><rdf:li>Family</rdf:li>
   </rdf:Bag></dc:subject>
   <lr:hierarchicalSubject><rdf:Bag>
    <rdf:li>darktable|format|jpg</rdf:li>
    <rdf:li>Places|Europe|France|Paris</rdf:li>
    <rdf:li>Family</rdf:li>
   </rdf:Bag></lr:hierarchicalSubject>
   <darktable:history><rdf:Seq/></darktable:history>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""


def _make_library(root: Path, excluded_tag_prefixes: list[str] | None = None) -> LibraryConfig:
    (root / "001.jpg").write_bytes(_MINIMAL_JPEG)
    (root / "001.jpg.xmp").write_text(_DARKTABLE_SIDECAR, encoding="utf-8")
    return LibraryConfig.create(
        name="hierarchical-tags",
        path=str(root),
        excluded_tag_prefixes=excluded_tag_prefixes,
    )


async def _index(agent_client: AgentClient, library: LibraryConfig) -> None:
    result = await agent_client.call_tool(
        "whitebeard",
        "index_library",
        {"force_extract_exif": False, "generate_thumbnails": False},
        library,
    )
    assert result["totalErrors"] == 0, result


async def _search_tags(
    agent_client: AgentClient, library: LibraryConfig, *values: str
) -> list[list[str]]:
    """Tags of every photo matching all *values* (AND)."""
    result: dict[str, Any] = await agent_client.call_tool(
        "wally", "search_photos", {"filters": {"tags": list(values)}}, library
    )
    return [m.get("tags", []) for m in result["matches"]]


@pytest.mark.asyncio
async def test_darktable_sidecar_indexed_searched_and_summarized(
    agent_client: AgentClient, tmp_path: Path
) -> None:
    library = _make_library(tmp_path)
    await _index(agent_client, library)

    expected = [["Places|Europe|France|Paris", "Family"]]
    # Subtree, bare name at a deep level, full path, AND across tags, any case.
    assert await _search_tags(agent_client, library, "Places|Europe") == expected
    assert await _search_tags(agent_client, library, "Paris") == expected
    assert await _search_tags(agent_client, library, "Places|Europe|France|Paris") == expected
    assert await _search_tags(agent_client, library, "Places|Europe", "Family") == expected
    assert await _search_tags(agent_client, library, "places|europe") == expected
    # Not under that branch; a path must start at the root.
    assert await _search_tags(agent_client, library, "Places|Asia") == []
    assert await _search_tags(agent_client, library, "Europe|France") == []
    # darktable's automatic tags are excluded by default, levels included.
    assert await _search_tags(agent_client, library, "darktable") == []
    assert await _search_tags(agent_client, library, "jpg") == []

    summary: dict[str, Any] = await agent_client.call_tool(
        "wally", "get_summary", {"filters": {}}, library
    )
    assert summary["tags"]["counts"] == {
        "Family": 1,
        "Places": 1,
        "Places|Europe": 1,
        "Places|Europe|France": 1,
        "Places|Europe|France|Paris": 1,
    }

    # Indexing only reads the sidecar: darktable's data is left untouched.
    assert (tmp_path / "001.jpg.xmp").read_text(encoding="utf-8") == _DARKTABLE_SIDECAR


@pytest.mark.asyncio
async def test_excluded_tag_prefixes_reach_the_indexer(
    agent_client: AgentClient, tmp_path: Path
) -> None:
    """An empty excluded_tag_prefixes in the library config disables the filter."""
    library = _make_library(tmp_path, excluded_tag_prefixes=[])
    await _index(agent_client, library)

    assert await _search_tags(agent_client, library, "darktable|format") == [
        ["darktable|format|jpg", "Places|Europe|France|Paris", "Family"]
    ]
