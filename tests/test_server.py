"""Tests for WoofServer tool behaviour (without a real agent process)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import ResourceLink, TextContent

from woof.agent_client import AgentError
from woof.config import BackendConfig, WoofConfig
from woof.server import WoofServer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config(tmp_path: Path) -> WoofConfig:
    return WoofConfig(
        backends=[BackendConfig(name="testlib", type="local", path=str(tmp_path))],
        config_dir=tmp_path / ".woof",
    )


@pytest.fixture()
def server(config: WoofConfig) -> WoofServer:
    return WoofServer(config, http_port=9999)


def _make_matches(
    *,
    partitions: list[str] | None = None,
    dates: list[str | None] | None = None,
    ratings: list[int | None] | None = None,
) -> list[dict[str, Any]]:
    """Build minimal match dicts for testing."""
    partitions = partitions or ["2024/01"]
    n = len(partitions)
    dates = dates or [None] * n
    ratings = ratings or [None] * n
    return [
        {
            "partition": partitions[i],
            "filename": f"photo_{i}.jpg",
            "contentHash": f"hash{i}",
            "filePath": f"{partitions[i]}/photo_{i}.jpg",
            **({"dateTaken": dates[i]} if dates[i] is not None else {}),
            **({"rating": ratings[i]} if ratings[i] is not None else {}),
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# add_backend / list_backends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_backend(server: WoofServer, tmp_path: Path) -> None:
    new_path = str(tmp_path / "new")
    tool_fn = await _get_tool(server, "add_backend")
    result = await tool_fn(name="newlib", path=new_path)
    assert result["name"] == "newlib"
    assert server.config.get_backend("newlib") is not None


@pytest.mark.asyncio
async def test_list_backends(server: WoofServer) -> None:
    tool_fn = await _get_tool(server, "list_backends")
    result = await tool_fn()
    assert any(b["name"] == "testlib" for b in result["backends"])


# ---------------------------------------------------------------------------
# list_search_fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_search_fields_returns_fields(server: WoofServer) -> None:
    mock_fields = [
        {
            "name": "dateTaken",
            "type": "DATE_RANGE",
            "filterFormat": "...",
            "pruneable": True,
        }
    ]
    mock = AsyncMock(return_value={"fields": mock_fields})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "list_search_fields")
        result = await tool_fn()
    assert result == {"name": "testlib", "fields": mock_fields}
    mock.assert_called_once_with("wally", "list_search_fields", {}, server.config.backends[0])


@pytest.mark.asyncio
async def test_list_search_fields_explicit_backend(server: WoofServer) -> None:
    mock_fields = [{"name": "rating", "type": "INT_RANGE"}]
    mock = AsyncMock(return_value={"fields": mock_fields})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "list_search_fields")
        result = await tool_fn(backend_name="testlib")
    assert result == {"name": "testlib", "fields": mock_fields}


@pytest.mark.asyncio
async def test_list_search_fields_unknown_backend_raises(server: WoofServer) -> None:
    tool_fn = await _get_tool(server, "list_search_fields")
    with pytest.raises(ValueError, match="not found"):
        await tool_fn(backend_name="ghost")


@pytest.mark.asyncio
async def test_list_search_fields_no_backends_returns_empty(tmp_path: Path) -> None:
    config = WoofConfig(backends=[], config_dir=tmp_path / ".woof")
    server = WoofServer(config, http_port=9999)
    tool_fn = await _get_tool(server, "list_search_fields")
    result = await tool_fn()
    assert result == {}


@pytest.mark.asyncio
async def test_list_search_fields_wally_error_returns_empty_fields(
    server: WoofServer,
) -> None:
    mock = AsyncMock(side_effect=AgentError("wally down"))
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "list_search_fields")
        result = await tool_fn()
    assert result == {"name": "testlib", "fields": []}


# ---------------------------------------------------------------------------
# _get_fields (lazy cache)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fields_fetches_on_first_call(server: WoofServer) -> None:
    fields = [{"name": "rating", "type": "INT_RANGE"}]
    mock = AsyncMock(return_value={"fields": fields})
    with patch.object(server._agent, "call_tool", new=mock):
        result = await server._get_fields(server.config.backends[0])
    assert result == fields
    mock.assert_called_once()


@pytest.mark.asyncio
async def test_get_fields_reuses_cache_on_second_call(server: WoofServer) -> None:
    fields = [{"name": "rating", "type": "INT_RANGE"}]
    mock = AsyncMock(return_value={"fields": fields})
    with patch.object(server._agent, "call_tool", new=mock):
        await server._get_fields(server.config.backends[0])
        result = await server._get_fields(server.config.backends[0])
    assert result == fields
    assert mock.call_count == 1  # only fetched once


@pytest.mark.asyncio
async def test_get_fields_error_returns_empty_and_is_not_cached(
    server: WoofServer,
) -> None:
    mock = AsyncMock(side_effect=AgentError("wally down"))
    with patch.object(server._agent, "call_tool", new=mock):
        result = await server._get_fields(server.config.backends[0])
    assert result == []
    assert "testlib" not in server._backend_fields  # error must not be cached


@pytest.mark.asyncio
async def test_get_fields_retries_after_error(server: WoofServer) -> None:
    backend = server.config.backends[0]
    fields = [{"name": "dateTaken", "type": "DATE_RANGE"}]
    mock = AsyncMock(side_effect=[AgentError("down"), {"fields": fields}])
    with patch.object(server._agent, "call_tool", new=mock):
        first = await server._get_fields(backend)
        second = await server._get_fields(backend)
    assert first == []
    assert second == fields
    assert mock.call_count == 2


# ---------------------------------------------------------------------------
# index_backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_backend_calls_whitebeard(server: WoofServer) -> None:
    mock_result: dict[str, Any] = {"photosProcessed": 5, "errors": 0}
    mock = AsyncMock(return_value=mock_result)
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "index_backend")
        result = await tool_fn(
            ctx=None, backend_name="testlib", partition="", force_extract_exif=False
        )
        assert result == mock_result
        mock.assert_called_once()
        assert mock.call_args[0][0] == "whitebeard"
        assert mock.call_args[0][1] == "index_library"
        args = mock.call_args[0][2]
        assert args["generate_thumbnails"] is True
        assert args["force_extract_exif"] is False


@pytest.mark.asyncio
async def test_index_backend_with_partition(server: WoofServer) -> None:
    mock = AsyncMock(return_value={})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "index_backend")
        await tool_fn(
            ctx=None,
            backend_name="testlib",
            partition="2024/2024-07",
            force_extract_exif=False,
        )
        assert mock.call_args[0][1] == "index_partition"
        assert mock.call_args[0][2]["partition"] == "2024/2024-07"


@pytest.mark.asyncio
async def test_index_backend_unknown_backend(server: WoofServer) -> None:
    tool_fn = await _get_tool(server, "index_backend")
    with pytest.raises(ValueError, match="not found"):
        await tool_fn(ctx=None, backend_name="unknown", partition="", force_extract_exif=False)


@pytest.mark.asyncio
async def test_index_backend_agent_error_is_logged(
    server: WoofServer, caplog: pytest.LogCaptureFixture
) -> None:
    mock = AsyncMock(side_effect=AgentError("whitebeard crashed"))
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "index_backend")
        with caplog.at_level(logging.ERROR, logger="woof.server"):
            result = await tool_fn(
                ctx=None, backend_name="testlib", partition="", force_extract_exif=False
            )
    assert "error" in result
    assert any("whitebeard crashed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _search_stats
# ---------------------------------------------------------------------------

_DATE_FIELD = {"name": "dateTaken", "type": "DATE_RANGE"}
_RATING_FIELD = {"name": "rating", "type": "INT_RANGE"}
_ALL_FIELDS = [_DATE_FIELD, _RATING_FIELD]


def test_search_stats_empty() -> None:
    stats = WoofServer._search_stats([], _ALL_FIELDS)
    assert stats == {"count": 0, "partitions": {}, "dateTaken": None, "rating": None}


def test_search_stats_no_fields() -> None:
    stats = WoofServer._search_stats([])
    assert stats == {"count": 0, "partitions": {}}


def test_search_stats_partition_counts_sorted() -> None:
    matches = _make_matches(partitions=["2024/03", "2024/01", "2024/03", "2024/02"])
    stats = WoofServer._search_stats(matches)
    assert stats["partitions"] == {"2024/01": 1, "2024/02": 1, "2024/03": 2}
    assert list(stats["partitions"].keys()) == ["2024/01", "2024/02", "2024/03"]


def test_search_stats_date_range() -> None:
    dates = ["2024-01-10T12:00:00", "2024-03-05T08:30:00", "2024-02-20T00:00:00"]
    matches = _make_matches(partitions=["p"] * 3, dates=dates)
    stats = WoofServer._search_stats(matches, [_DATE_FIELD])
    assert stats["dateTaken"] == {
        "min": "2024-01-10T12:00:00",
        "max": "2024-03-05T08:30:00",
    }


def test_search_stats_rating_range() -> None:
    matches = _make_matches(partitions=["p"] * 6, ratings=[5, 3, 5, None, 3, 1])
    stats = WoofServer._search_stats(matches, [_RATING_FIELD])
    assert stats["rating"] == {"min": 1, "max": 5}
    assert stats["count"] == 6


def test_search_stats_no_dates_gives_none() -> None:
    matches = _make_matches()
    assert WoofServer._search_stats(matches, [_DATE_FIELD])["dateTaken"] is None


# ---------------------------------------------------------------------------
# search_photos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_photos_calls_wally(server: WoofServer) -> None:
    mock_result = {
        "matches": [],
        "partitionsScanned": 3,
        "partitionsPruned": 1,
        "errors": 0,
    }
    mock = AsyncMock(return_value=mock_result)
    filters = {"date": {"min": "2024"}, "rating": {"min": 4}}
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, backend_name="testlib", filters=filters)
        assert mock.call_args[0][0] == "wally"
        assert mock.call_args[0][1] == "search_photos"
        assert mock.call_args[0][2]["filters"] == filters


@pytest.mark.asyncio
async def test_search_photos_omits_filters_when_none(server: WoofServer) -> None:
    mock = AsyncMock(return_value={"matches": []})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, backend_name="testlib")
        args_passed = mock.call_args[0][2]
        assert "filters" not in args_passed
        assert args_passed["root"] == ""


@pytest.mark.asyncio
async def test_search_photos_returns_stats_and_token(server: WoofServer) -> None:
    matches = _make_matches(
        partitions=["2024/01", "2024/01", "2024/02"],
        dates=["2024-01-05T00:00:00", "2024-01-10T00:00:00", "2024-02-01T00:00:00"],
        ratings=[5, None, 3],
    )
    fields = [
        {"name": "dateTaken", "type": "DATE_RANGE"},
        {"name": "rating", "type": "INT_RANGE"},
    ]

    async def _side_effect(agent, tool, args, backend, **kwargs):
        if tool == "list_search_fields":
            return {"fields": fields}
        return {"matches": matches}

    with patch.object(server._agent, "call_tool", new=AsyncMock(side_effect=_side_effect)):
        tool_fn = await _get_tool(server, "search_photos")
        result = await tool_fn(ctx=None, backend_name="testlib")
    assert result["count"] == 3
    assert result["partitions"] == {"2024/01": 2, "2024/02": 1}
    assert result["dateTaken"] == {
        "min": "2024-01-05T00:00:00",
        "max": "2024-02-01T00:00:00",
    }
    assert result["rating"] == {"min": 3, "max": 5}
    assert "session_token" in result


@pytest.mark.asyncio
async def test_search_photos_stores_session(server: WoofServer) -> None:
    matches = _make_matches(partitions=["2024/01"])
    mock = AsyncMock(return_value={"matches": matches})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        result = await tool_fn(ctx=None, backend_name="testlib")
    token = result["session_token"]
    session = server._sessions.sessions[token]
    assert session["matches"] == matches
    assert session["backend"] == "testlib"
    assert session["httpPort"] == 9999


@pytest.mark.asyncio
async def test_search_photos_agent_error_is_logged(
    server: WoofServer, caplog: pytest.LogCaptureFixture
) -> None:
    mock = AsyncMock(side_effect=AgentError("wally exploded"))
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        with caplog.at_level(logging.ERROR, logger="woof.server"):
            result = await tool_fn(ctx=None, backend_name="testlib")
    assert "error" in result
    assert "wally exploded" in result["error"]
    assert any("wally exploded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# browse_gallery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_gallery_unknown_token(server: WoofServer) -> None:
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=["bad-token"])
    assert "error" in result


@pytest.mark.asyncio
async def test_browse_gallery_returns_session_matches(server: WoofServer) -> None:
    matches = _make_matches(partitions=["2024/01", "2024/01"])
    token = "test-token"
    server._sessions.sessions[token] = {
        "matches": matches,
        "backend": "testlib",
        "httpPort": 9999,
        "querySummary": "",
    }
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=[token], query_summary="My query")
    assert result["matches"] == matches
    assert result["backend"] == "testlib"
    assert result["querySummary"] == "My query"
    assert result["httpPort"] == 9999


@pytest.mark.asyncio
async def test_browse_gallery_sets_query_summary(server: WoofServer) -> None:
    token = "tok"
    server._sessions.sessions[token] = {
        "matches": [],
        "backend": "testlib",
        "httpPort": 9999,
        "querySummary": "",
    }
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=[token], query_summary="Summer 2024")
    merged_token = result["galleryUrl"].rstrip("/").split("/")[-1]
    assert server._sessions.sessions[merged_token]["querySummary"] == "Summer 2024"


@pytest.mark.asyncio
async def test_browse_gallery_merges_and_deduplicates(server: WoofServer) -> None:
    matches_a = _make_matches(partitions=["2024/01", "2024/02"])  # hash0, hash1
    matches_b = _make_matches(partitions=["2024/02", "2024/03"])  # hash0 (dup), hash1 (dup)
    # Override hashes so session B shares hash0 with session A but has a unique hash2
    matches_b[0]["contentHash"] = "hash0"  # duplicate
    matches_b[1]["contentHash"] = "hash2"  # unique

    server._sessions.sessions["tok-a"] = {
        "matches": matches_a,
        "backend": "lib1",
        "querySummary": "",
    }
    server._sessions.sessions["tok-b"] = {
        "matches": matches_b,
        "backend": "lib2",
        "querySummary": "",
    }

    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=["tok-a", "tok-b"], query_summary="")

    hashes = [m["contentHash"] for m in result["matches"]]
    assert hashes == ["hash0", "hash1", "hash2"]
    assert result["backend"] == "lib1, lib2"


@pytest.mark.asyncio
async def test_browse_gallery_partial_unknown_token(server: WoofServer) -> None:
    server._sessions.sessions["good"] = {"matches": [], "backend": "lib", "querySummary": ""}
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=["good", "missing"])
    assert "error" in result
    assert "missing" in result["error"]


@pytest.mark.asyncio
async def test_browse_gallery_returns_session_token(server: WoofServer) -> None:
    """browse_gallery must include sessionToken so the gallery can call share_photos."""
    server._sessions.sessions["tok"] = {
        "matches": [],
        "backend": "testlib",
        "httpPort": 9999,
        "querySummary": "",
    }
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=["tok"])
    assert "sessionToken" in result
    assert isinstance(result["sessionToken"], str) and result["sessionToken"]


# ---------------------------------------------------------------------------
# share_photos_with_claude
# ---------------------------------------------------------------------------


def _session_with_matches(*hashes: str, backend: str = "testlib") -> dict[str, Any]:
    """Build a minimal session dict for share_photos tests."""
    return {
        "matches": [
            {
                "contentHash": h,
                "partition": "2024/01",
                "filename": f"{h}.jpg",
                "filePath": f"2024/01/{h}.jpg",
            }
            for h in hashes
        ],
        "backend": backend,
        "httpPort": 9999,
        "querySummary": "",
    }


@pytest.mark.asyncio
async def test_share_photos_returns_resource_links(server: WoofServer) -> None:
    """Each photo is returned as a ResourceLink pointing to photos://preview/..."""
    server._sessions.sessions["tok"] = _session_with_matches("h1", "h2")
    tool_fn = await _get_tool(server, "share_photos_with_claude")
    result = await tool_fn(session_token="tok", content_hashes=["h1", "h2"])
    links = [item for item in result if isinstance(item, ResourceLink)]
    assert len(links) == 2
    assert str(links[0].uri) == "photos://preview/tok/h1"
    assert links[0].mimeType == "image/jpeg"
    assert links[0].name == "h1.jpg"
    assert str(links[1].uri) == "photos://preview/tok/h2"


@pytest.mark.asyncio
async def test_share_photos_includes_metadata_text(server: WoofServer) -> None:
    session = _session_with_matches("h1")
    session["matches"][0].update(
        {"dateTaken": "2024-07-15T14:32:00", "make": "Canon", "model": "EOS R5"}
    )
    server._sessions.sessions["tok"] = session
    tool_fn = await _get_tool(server, "share_photos_with_claude")
    result = await tool_fn(session_token="tok", content_hashes=["h1"])
    texts = [item.text for item in result if isinstance(item, TextContent)]
    meta = next(t for t in texts if "h1.jpg" in t)
    assert "2024-07-15" in meta
    assert "Canon EOS R5" in meta


@pytest.mark.asyncio
async def test_share_photos_includes_tags_in_metadata(server: WoofServer) -> None:
    session = _session_with_matches("h1")
    session["matches"][0]["tags"] = ["beach", "sunset"]
    server._sessions.sessions["tok"] = session
    tool_fn = await _get_tool(server, "share_photos_with_claude")
    result = await tool_fn(session_token="tok", content_hashes=["h1"])
    texts = [item.text for item in result if isinstance(item, TextContent)]
    meta = next(t for t in texts if "h1.jpg" in t)
    assert "beach" in meta and "sunset" in meta


@pytest.mark.asyncio
async def test_share_photos_unknown_token_raises(server: WoofServer) -> None:
    tool_fn = await _get_tool(server, "share_photos_with_claude")
    with pytest.raises(ValueError, match="Unknown session token"):
        await tool_fn(session_token="ghost", content_hashes=["h1"])


@pytest.mark.asyncio
async def test_share_photos_empty_hashes_raises(server: WoofServer) -> None:
    server._sessions.sessions["tok"] = _session_with_matches("h1")
    tool_fn = await _get_tool(server, "share_photos_with_claude")
    with pytest.raises(ValueError, match="No photos selected"):
        await tool_fn(session_token="tok", content_hashes=[])


@pytest.mark.asyncio
async def test_share_photos_too_many_hashes_raises(server: WoofServer) -> None:
    hashes = [f"h{i}" for i in range(11)]
    server._sessions.sessions["tok"] = _session_with_matches(*hashes)
    tool_fn = await _get_tool(server, "share_photos_with_claude")
    with pytest.raises(ValueError, match="10 photos"):
        await tool_fn(session_token="tok", content_hashes=hashes)


@pytest.mark.asyncio
async def test_share_photos_unmatched_hashes_raises(server: WoofServer) -> None:
    server._sessions.sessions["tok"] = _session_with_matches("h1")
    tool_fn = await _get_tool(server, "share_photos_with_claude")
    with pytest.raises(ValueError, match="found in this session"):
        await tool_fn(session_token="tok", content_hashes=["ghost"])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _get_tool(server: WoofServer, name: str) -> Any:
    """Extract a tool function from the FastMCP registry by name."""
    tool = await server.mcp.get_tool(name)
    return tool.fn
