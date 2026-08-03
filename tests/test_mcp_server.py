"""Tests for McpServer tool behaviour (without a real agent process)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from woof.agent_client import AgentClient, AgentError
from woof.config import LibraryConfig, WoofConfig
from woof.gallery_session_manager import GallerySessionManager, SessionHandler
from woof.indexing_session_manager import IndexingSessionManager
from woof.mcp_server import McpServer

# These tests never serve real HTTP (they call tool functions directly), so a
# fixed, unbound pair of URLs is enough — McpServer has no socket of its own.
_TEST_SERVER_URLS = ["http://localhost:54321", "http://127.0.0.1:54321"]


def _make_server(config: WoofConfig) -> McpServer:
    return McpServer(
        config,
        server_urls=_TEST_SERVER_URLS,
        agent_client=AgentClient(),
        session_manager=GallerySessionManager(),
        indexing_session_manager=IndexingSessionManager(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config(tmp_path: Path) -> WoofConfig:
    return WoofConfig(
        libraries=[LibraryConfig(name="testlib", type="local", path=str(tmp_path))],
        config_dir=tmp_path / ".woof",
    )


@pytest.fixture()
def server(config: WoofConfig) -> McpServer:
    return _make_server(config)


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
            **({"dateTaken": dates[i]} if dates[i] is not None else {}),
            **({"rating": ratings[i]} if ratings[i] is not None else {}),
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# add_library / list_libraries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_library(server: McpServer, tmp_path: Path) -> None:
    new_path = str(tmp_path / "new")
    tool_fn = await _get_tool(server, "add_library")
    result = await tool_fn(name="newlib", path=new_path)
    assert result["name"] == "newlib"
    assert server.config.get_library("newlib") is not None


@pytest.mark.asyncio
async def test_list_libraries(server: McpServer) -> None:
    tool_fn = await _get_tool(server, "list_libraries")
    result = await tool_fn()
    assert any(b["name"] == "testlib" for b in result["libraries"])


# ---------------------------------------------------------------------------
# list_search_fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_search_fields_returns_fields(server: McpServer) -> None:
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
    mock.assert_called_once_with("wally", "list_search_fields", {}, server.config.libraries[0])


@pytest.mark.asyncio
async def test_list_search_fields_explicit_backend(server: McpServer) -> None:
    mock_fields = [{"name": "rating", "type": "INT_RANGE"}]
    mock = AsyncMock(return_value={"fields": mock_fields})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "list_search_fields")
        result = await tool_fn(library_name="testlib")
    assert result == {"name": "testlib", "fields": mock_fields}


@pytest.mark.asyncio
async def test_list_search_fields_unknown_library_raises(server: McpServer) -> None:
    tool_fn = await _get_tool(server, "list_search_fields")
    with pytest.raises(ValueError, match="not found"):
        await tool_fn(library_name="ghost")


@pytest.mark.asyncio
async def test_list_search_fields_no_libraries_returns_empty(tmp_path: Path) -> None:
    config = WoofConfig(libraries=[], config_dir=tmp_path / ".woof")
    server = _make_server(config)
    tool_fn = await _get_tool(server, "list_search_fields")
    result = await tool_fn()
    assert result == {}


@pytest.mark.asyncio
async def test_list_search_fields_wally_error_returns_empty_fields(
    server: McpServer,
) -> None:
    mock = AsyncMock(side_effect=AgentError("wally down"))
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "list_search_fields")
        result = await tool_fn()
    assert result == {"name": "testlib", "fields": []}


@pytest.mark.asyncio
async def test_list_search_fields_propagates_full_text_search(server: McpServer) -> None:
    """full_text_search block from Wally must be passed through to the caller."""
    fts_block = {
        "description": "Search text fields with a single query string.",
        "fields": [{"name": "description", "column": "description", "label": "Description"}],
    }
    mock = AsyncMock(
        return_value={
            "fields": [{"name": "rating", "type": "INT_RANGE"}],
            "full_text_search": fts_block,
        }
    )
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "list_search_fields")
        result = await tool_fn()
    assert result["full_text_search"] == fts_block


# ---------------------------------------------------------------------------
# _get_fields_raw (lazy cache)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fields_raw_fetches_on_first_call(server: McpServer) -> None:
    fields = [{"name": "rating", "type": "INT_RANGE"}]
    mock = AsyncMock(return_value={"fields": fields})
    with patch.object(server._agent, "call_tool", new=mock):
        result = await server._get_fields_raw(server.config.libraries[0])
    assert result == {"fields": fields}
    mock.assert_called_once()


@pytest.mark.asyncio
async def test_get_fields_raw_reuses_cache_on_second_call(server: McpServer) -> None:
    fields = [{"name": "rating", "type": "INT_RANGE"}]
    mock = AsyncMock(return_value={"fields": fields})
    with patch.object(server._agent, "call_tool", new=mock):
        await server._get_fields_raw(server.config.libraries[0])
        result = await server._get_fields_raw(server.config.libraries[0])
    assert result == {"fields": fields}
    assert mock.call_count == 1  # only fetched once


@pytest.mark.asyncio
async def test_get_fields_raw_error_returns_empty_and_is_not_cached(
    server: McpServer,
) -> None:
    mock = AsyncMock(side_effect=AgentError("wally down"))
    with patch.object(server._agent, "call_tool", new=mock):
        result = await server._get_fields_raw(server.config.libraries[0])
    assert result == {"fields": []}
    assert "testlib" not in server._library_fields  # error must not be cached


@pytest.mark.asyncio
async def test_get_fields_raw_retries_after_error(server: McpServer) -> None:
    library = server.config.libraries[0]
    fields = [{"name": "dateTaken", "type": "DATE_RANGE"}]
    mock = AsyncMock(side_effect=[AgentError("down"), {"fields": fields}])
    with patch.object(server._agent, "call_tool", new=mock):
        first = await server._get_fields_raw(library)
        second = await server._get_fields_raw(library)
    assert first == {"fields": []}
    assert second == {"fields": fields}
    assert mock.call_count == 2


# ---------------------------------------------------------------------------
# index_library
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_library_launches_background_task(server: McpServer) -> None:
    """index_library returns immediately with type='indexing' and a session_id."""
    captured: dict[str, Any] = {}

    def mock_background(
        module, tool_name, args, library, *, on_progress=None, on_complete=None, on_error=None
    ):
        captured["module"] = module
        captured["tool_name"] = tool_name
        captured["args"] = args
        return None  # no real task

    with patch.object(server._agent, "call_tool_background", side_effect=mock_background):
        tool_fn = await _get_tool(server, "index_library")
        result = await tool_fn(library_name="testlib", partition="", force_extract_exif=False)

    assert result["type"] == "indexing"
    assert "session_id" in result
    assert result["library_name"] == "testlib"
    assert result["serverUrls"] == server.server_urls
    assert captured["module"] == "whitebeard"
    assert captured["tool_name"] == "index_library"
    assert captured["args"]["generate_thumbnails"] is True
    assert captured["args"]["force_extract_exif"] is False


@pytest.mark.asyncio
async def test_index_library_with_partition(server: McpServer) -> None:
    captured: dict[str, Any] = {}

    def mock_background(
        module, tool_name, args, library, *, on_progress=None, on_complete=None, on_error=None
    ):
        captured["tool_name"] = tool_name
        captured["args"] = args
        return None

    with patch.object(server._agent, "call_tool_background", side_effect=mock_background):
        tool_fn = await _get_tool(server, "index_library")
        result = await tool_fn(
            library_name="testlib", partition="2024/2024-07", force_extract_exif=False
        )

    assert captured["tool_name"] == "index_partition"
    assert captured["args"]["partition"] == "2024/2024-07"
    assert result["partition"] == "2024/2024-07"


@pytest.mark.asyncio
async def test_index_library_unknown_library(server: McpServer) -> None:
    tool_fn = await _get_tool(server, "index_library")
    with pytest.raises(ValueError, match="not found"):
        await tool_fn(library_name="unknown", partition="", force_extract_exif=False)


@pytest.mark.asyncio
async def test_index_library_callbacks_update_session(server: McpServer) -> None:
    """on_progress / on_complete callbacks wire into the indexing session manager."""
    callbacks: dict[str, Any] = {}

    def mock_background(
        module, tool_name, args, library, *, on_progress=None, on_complete=None, on_error=None
    ):
        callbacks["on_progress"] = on_progress
        callbacks["on_complete"] = on_complete
        callbacks["on_error"] = on_error
        return None

    with patch.object(server._agent, "call_tool_background", side_effect=mock_background):
        tool_fn = await _get_tool(server, "index_library")
        result = await tool_fn(library_name="testlib", partition="", force_extract_exif=False)

    sid = result["session_id"]
    callbacks["on_progress"](42.0, 100.0, "msg")
    s = server._indexing_sessions.get(sid)
    assert s["progress"] == 42.0

    callbacks["on_complete"]({"photosIndexed": 7})
    s = server._indexing_sessions.get(sid)
    assert s["status"] == "completed"
    assert s["summary"]["photosIndexed"] == 7


@pytest.mark.asyncio
async def test_index_library_registers_task(server: McpServer) -> None:
    """index_library registers the returned task with the session manager."""
    import asyncio
    from unittest.mock import MagicMock

    fake_task = MagicMock(spec=asyncio.Task)

    def mock_background(
        module, tool_name, args, library, *, on_progress=None, on_complete=None, on_error=None
    ):
        return fake_task

    with patch.object(server._agent, "call_tool_background", side_effect=mock_background):
        tool_fn = await _get_tool(server, "index_library")
        result = await tool_fn(library_name="testlib", partition="", force_extract_exif=False)

    sid = result["session_id"]
    assert server._indexing_sessions._tasks[sid] is fake_task


@pytest.mark.asyncio
async def test_index_library_on_error_cancelled_calls_cancelled(server: McpServer) -> None:
    """_on_error with CancelledError transitions session to 'cancelled', not 'failed'."""
    import asyncio

    callbacks: dict[str, Any] = {}

    def mock_background(
        module, tool_name, args, library, *, on_progress=None, on_complete=None, on_error=None
    ):
        callbacks["on_error"] = on_error
        return None

    with patch.object(server._agent, "call_tool_background", side_effect=mock_background):
        tool_fn = await _get_tool(server, "index_library")
        result = await tool_fn(library_name="testlib", partition="", force_extract_exif=False)

    sid = result["session_id"]
    callbacks["on_error"](asyncio.CancelledError())
    s = server._indexing_sessions.get(sid)
    assert s["status"] == "cancelled"


# ---------------------------------------------------------------------------
# search_photos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_photos_calls_wally(server: McpServer) -> None:
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
        await tool_fn(ctx=None, library_name="testlib", filters=filters)
        assert mock.call_args[0][0] == "wally"
        assert mock.call_args[0][1] == "search_photos"
        assert mock.call_args[0][2]["filters"] == filters


@pytest.mark.asyncio
async def test_search_photos_coerces_stringified_filters(server: McpServer) -> None:
    """CoWork's MCP client serializes object-typed args as JSON strings; accept both."""
    mock = AsyncMock(return_value={"matches": []})
    filters = {"date": {"min": "2024"}, "rating": {"min": 4}}
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, library_name="testlib", filters=json.dumps(filters))
        assert mock.call_args[0][2]["filters"] == filters


@pytest.mark.asyncio
async def test_search_photos_stringified_filters_malformed_raises(server: McpServer) -> None:
    mock = AsyncMock(return_value={"matches": []})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        with pytest.raises(ValueError, match="filters"):
            await tool_fn(ctx=None, library_name="testlib", filters="not json")


@pytest.mark.asyncio
async def test_search_photos_omits_filters_when_none(server: McpServer) -> None:
    mock = AsyncMock(return_value={"matches": []})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, library_name="testlib")
        args_passed = mock.call_args[0][2]
        assert "filters" not in args_passed
        assert "partitions" not in args_passed


@pytest.mark.asyncio
async def test_search_photos_forwards_full_text_filter(server: McpServer) -> None:
    """full_text_filter must be forwarded to Wally verbatim."""
    mock = AsyncMock(return_value={"matches": []})
    fts = {"query": "Canyon", "columns": ["description"]}
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, library_name="testlib", full_text_filter=fts)
        args_passed = mock.call_args[0][2]
        assert args_passed["full_text_filter"] == fts


@pytest.mark.asyncio
async def test_search_photos_coerces_stringified_full_text_filter(server: McpServer) -> None:
    mock = AsyncMock(return_value={"matches": []})
    fts = {"query": "Canyon", "columns": ["description"]}
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, library_name="testlib", full_text_filter=json.dumps(fts))
        assert mock.call_args[0][2]["full_text_filter"] == fts


@pytest.mark.asyncio
async def test_search_photos_omits_full_text_filter_when_none(server: McpServer) -> None:
    mock = AsyncMock(return_value={"matches": []})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, library_name="testlib")
        args_passed = mock.call_args[0][2]
        assert "full_text_filter" not in args_passed


@pytest.mark.asyncio
async def test_search_photos_returns_total_count_and_token(server: McpServer) -> None:
    matches = _make_matches(
        partitions=["2024/01", "2024/01", "2024/02"],
        dates=["2024-01-05T00:00:00", "2024-01-10T00:00:00", "2024-02-01T00:00:00"],
        ratings=[5, None, 3],
    )
    mock = AsyncMock(return_value={"matches": matches, "totalCount": 3})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        result = await tool_fn(ctx=None, library_name="testlib")
    assert result["totalCount"] == 3
    assert "pageStats" not in result
    assert "session_token" in result


@pytest.mark.asyncio
async def test_search_photos_stores_session(server: McpServer) -> None:
    matches = _make_matches(partitions=["2024/01"])
    mock = AsyncMock(return_value={"matches": matches})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        result = await tool_fn(ctx=None, library_name="testlib")
    token = result["session_token"]
    session = server._sessions.sessions[token]
    assert all(m["library"] == "testlib" for m in session.matches)
    assert [m["partition"] for m in session.matches] == [m["partition"] for m in matches]


@pytest.mark.asyncio
async def test_search_photos_agent_error_is_logged(
    server: McpServer, caplog: pytest.LogCaptureFixture
) -> None:
    mock = AsyncMock(side_effect=AgentError("wally exploded"))
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        with caplog.at_level(logging.ERROR, logger="woof.mcp_server"):
            result = await tool_fn(ctx=None, library_name="testlib")
    assert "error" in result
    assert "wally exploded" in result["error"]
    assert any("wally exploded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# browse_gallery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_gallery_unknown_token(server: McpServer) -> None:
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=["bad-token"])
    assert "error" in result


@pytest.mark.asyncio
async def test_browse_gallery_coerces_stringified_session_tokens(server: McpServer) -> None:
    """CoWork's MCP client serializes array-typed args as JSON strings; accept both."""
    matches = _make_matches(partitions=["2024/01"])
    token = "test-token"
    server._sessions.sessions[token] = SessionHandler(
        library=LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent=None,
        queryArgs={},
        pageSize=400,
        totalCount=1,
        matches=matches,
    )
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=json.dumps([token]))
    assert "error" not in result
    assert result["totalCount"] == len(matches)


@pytest.mark.asyncio
async def test_browse_gallery_returns_session_matches(server: McpServer) -> None:
    matches = _make_matches(partitions=["2024/01", "2024/01"])
    token = "test-token"
    server._sessions.sessions[token] = SessionHandler(
        library=LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent=None,
        queryArgs={},
        pageSize=400,
        totalCount=2,
        matches=matches,
    )
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=[token], query_summary="My query")
    # browse_gallery no longer returns matches inline — only a token so the
    # gallery fetches directly from the HTTP server (OEC#19).
    assert "matches" not in result
    assert result["serverUrl"] == server.server_url
    assert result["serverUrls"] == server.server_urls
    assert result["querySummary"] == "My query"
    assert result["totalCount"] == len(matches)
    merged_token = result["token"]
    assert server._sessions.sessions[merged_token].matches == matches


@pytest.mark.asyncio
async def test_browse_gallery_merges_and_deduplicates(server: McpServer) -> None:
    matches_a = _make_matches(partitions=["2024/01", "2024/02"])  # hash0, hash1
    matches_b = _make_matches(partitions=["2024/02", "2024/03"])  # hash0 (dup), hash1 (dup)
    # Override hashes so session B shares hash0 with session A but has a unique hash2
    matches_b[0]["contentHash"] = "hash0"  # duplicate
    matches_b[1]["contentHash"] = "hash2"  # unique

    server._sessions.sessions["tok-a"] = SessionHandler(
        library=LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent=None,
        queryArgs={},
        pageSize=400,
        totalCount=2,
        matches=matches_a,
    )
    server._sessions.sessions["tok-b"] = SessionHandler(
        library=LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent=None,
        queryArgs={},
        pageSize=400,
        totalCount=2,
        matches=matches_b,
    )

    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=["tok-a", "tok-b"], query_summary="")

    # Matches are stored in the merged session, not returned inline.
    assert "matches" not in result
    merged_token = result["token"]
    merged_matches = server._sessions.sessions[merged_token].matches
    hashes = [m["contentHash"] for m in merged_matches]
    assert hashes == ["hash0", "hash1", "hash2"]
    assert result["totalCount"] == 3


@pytest.mark.asyncio
async def test_browse_gallery_partial_unknown_token(server: McpServer) -> None:
    server._sessions.sessions["good"] = {"matches": [], "backend": "lib", "querySummary": ""}
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_tokens=["good", "missing"])
    assert "error" in result
    assert "missing" in result["error"]


# ---------------------------------------------------------------------------
# server_urls / gallery CSP
# ---------------------------------------------------------------------------


def test_server_urls_includes_localhost_and_loopback_ip(server: McpServer) -> None:
    # Different MCP hosts accept different loopback hostnames in their CSP
    # (Claude Desktop Chat requires "localhost", Claude CoWork requires
    # "127.0.0.1") — both must be offered for the same port.
    port = server.server_url.rsplit(":", 1)[1]
    assert server.server_urls == [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    assert server.server_url == server.server_urls[0]


@pytest.mark.asyncio
async def test_gallery_resource_csp_declares_all_server_urls(server: McpServer) -> None:
    resources = await server.mcp.list_resources()
    gallery = next(r for r in resources if str(r.uri) == "ui://gallery/ouestcharlie")
    csp = gallery.meta["ui"]["csp"]
    assert csp["resourceDomains"] == server.server_urls
    assert csp["connectDomains"] == server.server_urls


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _get_tool(server: McpServer, name: str) -> Any:
    """Extract a tool function from the FastMCP registry by name."""
    tool = await server.mcp.get_tool(name)
    return tool.fn
