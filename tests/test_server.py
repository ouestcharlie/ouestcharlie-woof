"""Tests for WoofServer tool behaviour (without a real agent process)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

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
# add_backend / list_backends / get_status
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


@pytest.mark.asyncio
async def test_get_status_existing_backend(server: WoofServer, tmp_path: Path) -> None:
    tool_fn = await _get_tool(server, "get_status")
    result = await tool_fn()
    entry = next(s for s in result["backends"] if s["name"] == "testlib")
    assert entry["exists"] is True


@pytest.mark.asyncio
async def test_get_status_missing_backend(config: WoofConfig) -> None:
    config.backends = [BackendConfig(name="ghost", type="local", path="/nonexistent")]
    server = WoofServer(config, http_port=9999)
    tool_fn = await _get_tool(server, "get_status")
    result = await tool_fn()
    entry = next(s for s in result["backends"] if s["name"] == "ghost")
    assert entry["exists"] is False


# ---------------------------------------------------------------------------
# index_backend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_backend_calls_whitebeard(server: WoofServer) -> None:
    mock_result: dict[str, Any] = {"photosProcessed": 5, "errors": 0}
    mock = AsyncMock(return_value=mock_result)
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "index_backend")
        result = await tool_fn(ctx=None, backend_name="testlib", partition="", force=False)
        assert result == mock_result
        mock.assert_called_once()
        assert mock.call_args[0][0] == "whitebeard"
        assert mock.call_args[0][1] == "index_library_tool"


@pytest.mark.asyncio
async def test_index_backend_with_partition(server: WoofServer) -> None:
    mock = AsyncMock(return_value={})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "index_backend")
        await tool_fn(ctx=None, backend_name="testlib", partition="2024/2024-07", force=False)
        assert mock.call_args[0][1] == "index_partition_tool"
        assert mock.call_args[0][2]["partition"] == "2024/2024-07"


@pytest.mark.asyncio
async def test_index_backend_unknown_backend(server: WoofServer) -> None:
    tool_fn = await _get_tool(server, "index_backend")
    with pytest.raises(ValueError, match="not found"):
        await tool_fn(ctx=None, backend_name="unknown", partition="", force=False)


@pytest.mark.asyncio
async def test_index_backend_agent_error_is_logged(
    server: WoofServer, caplog: pytest.LogCaptureFixture
) -> None:
    mock = AsyncMock(side_effect=AgentError("whitebeard crashed"))
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "index_backend")
        with caplog.at_level(logging.ERROR, logger="woof.server"):
            result = await tool_fn(ctx=None, backend_name="testlib", partition="", force=False)
    assert "error" in result
    assert any("whitebeard crashed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _search_stats
# ---------------------------------------------------------------------------

def test_search_stats_empty() -> None:
    stats = WoofServer._search_stats([])
    assert stats == {
        "count": 0,
        "partitions": {},
        "date_range": None,
        "rating_distribution": {},
    }


def test_search_stats_partition_counts_sorted() -> None:
    matches = _make_matches(partitions=["2024/03", "2024/01", "2024/03", "2024/02"])
    stats = WoofServer._search_stats(matches)
    assert stats["partitions"] == {"2024/01": 1, "2024/02": 1, "2024/03": 2}
    assert list(stats["partitions"].keys()) == ["2024/01", "2024/02", "2024/03"]


def test_search_stats_date_range() -> None:
    dates = ["2024-01-10T12:00:00", "2024-03-05T08:30:00", "2024-02-20T00:00:00"]
    matches = _make_matches(partitions=["p"] * 3, dates=dates)
    stats = WoofServer._search_stats(matches)
    assert stats["date_range"] == {
        "earliest": "2024-01-10T12:00:00",
        "latest": "2024-03-05T08:30:00",
    }


def test_search_stats_rating_distribution() -> None:
    matches = _make_matches(partitions=["p"] * 6, ratings=[5, 3, 5, None, 3, 1])
    stats = WoofServer._search_stats(matches)
    assert stats["rating_distribution"] == {"1": 1, "3": 2, "5": 2}
    assert stats["count"] == 6


def test_search_stats_no_dates_gives_none_range() -> None:
    matches = _make_matches()
    assert WoofServer._search_stats(matches)["date_range"] is None


# ---------------------------------------------------------------------------
# search_photos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_photos_calls_wally(server: WoofServer) -> None:
    mock_result = {"matches": [], "partitionsScanned": 3, "partitionsPruned": 1, "errors": 0}
    mock = AsyncMock(return_value=mock_result)
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, backend_name="testlib", date_min="2024")
        assert mock.call_args[0][0] == "wally"
        assert mock.call_args[0][1] == "search_photos_tool"
        assert mock.call_args[0][2]["date_min"] == "2024"


@pytest.mark.asyncio
async def test_search_photos_omits_none_params(server: WoofServer) -> None:
    mock = AsyncMock(return_value={"matches": []})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        await tool_fn(ctx=None, backend_name="testlib")
        args_passed = mock.call_args[0][2]
        assert "date_min" not in args_passed
        assert "tags" not in args_passed


@pytest.mark.asyncio
async def test_search_photos_returns_stats_and_token(server: WoofServer) -> None:
    matches = _make_matches(
        partitions=["2024/01", "2024/01", "2024/02"],
        dates=["2024-01-05T00:00:00", "2024-01-10T00:00:00", "2024-02-01T00:00:00"],
        ratings=[5, None, 3],
    )
    mock = AsyncMock(return_value={"matches": matches})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        result = await tool_fn(ctx=None, backend_name="testlib")
    assert result["count"] == 3
    assert result["partitions"] == {"2024/01": 2, "2024/02": 1}
    assert result["date_range"]["earliest"] == "2024-01-05T00:00:00"
    assert result["date_range"]["latest"] == "2024-02-01T00:00:00"
    assert result["rating_distribution"] == {"3": 1, "5": 1}
    assert "session_token" in result


@pytest.mark.asyncio
async def test_search_photos_stores_session(server: WoofServer) -> None:
    matches = _make_matches(partitions=["2024/01"])
    mock = AsyncMock(return_value={"matches": matches})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = await _get_tool(server, "search_photos")
        result = await tool_fn(ctx=None, backend_name="testlib")
    token = result["session_token"]
    session = server._gallery_sessions[token]
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
    result = await tool_fn(session_token="bad-token")
    assert "error" in result


@pytest.mark.asyncio
async def test_browse_gallery_returns_session_matches(server: WoofServer) -> None:
    matches = _make_matches(partitions=["2024/01", "2024/01"])
    token = "test-token"
    server._gallery_sessions[token] = {
        "matches": matches,
        "backend": "testlib",
        "httpPort": 9999,
        "querySummary": "",
    }
    tool_fn = await _get_tool(server, "browse_gallery")
    result = await tool_fn(session_token=token, query_summary="My query")
    assert result["matches"] == matches
    assert result["backend"] == "testlib"
    assert result["querySummary"] == "My query"
    assert result["httpPort"] == 9999


@pytest.mark.asyncio
async def test_browse_gallery_sets_query_summary(server: WoofServer) -> None:
    token = "tok"
    server._gallery_sessions[token] = {
        "matches": [],
        "backend": "testlib",
        "httpPort": 9999,
        "querySummary": "",
    }
    tool_fn = await _get_tool(server, "browse_gallery")
    await tool_fn(session_token=token, query_summary="Summer 2024")
    assert server._gallery_sessions[token]["querySummary"] == "Summer 2024"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _get_tool(server: WoofServer, name: str) -> Any:
    """Extract a tool function from the FastMCP registry by name."""
    tool = await server.mcp.get_tool(name)
    return tool.fn
