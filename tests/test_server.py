"""Tests for WoofServer tool behaviour (without a real agent process)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from woof.config import BackendConfig, WoofConfig
from woof.server import WoofServer


@pytest.fixture()
def config(tmp_path: Path) -> WoofConfig:
    return WoofConfig(
        backends=[BackendConfig(name="testlib", type="local", path=str(tmp_path))]
    )


@pytest.fixture()
def server(config: WoofConfig) -> WoofServer:
    return WoofServer(config, http_port=9999)


# ------------------------------------------------------------------
# add_backend / list_backends / get_status
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_backend(server: WoofServer, tmp_path: Path) -> None:
    new_path = str(tmp_path / "new")
    # Call the tool function directly by finding it in the FastMCP registry
    tool_fn = _get_tool(server, "add_backend")
    result = await tool_fn(name="newlib", path=new_path)
    assert result["name"] == "newlib"
    assert server.config.get_backend("newlib") is not None


@pytest.mark.asyncio
async def test_list_backends(server: WoofServer) -> None:
    tool_fn = _get_tool(server, "list_backends")
    result = await tool_fn()
    assert any(b["name"] == "testlib" for b in result["backends"])


@pytest.mark.asyncio
async def test_get_status_existing_backend(server: WoofServer, tmp_path: Path) -> None:
    tool_fn = _get_tool(server, "get_status")
    result = await tool_fn()
    entry = next(s for s in result["backends"] if s["name"] == "testlib")
    assert entry["exists"] is True  # tmp_path exists


@pytest.mark.asyncio
async def test_get_status_missing_backend(config: WoofConfig) -> None:
    config.backends = [BackendConfig(name="ghost", type="local", path="/nonexistent")]
    server = WoofServer(config, http_port=9999)
    tool_fn = _get_tool(server, "get_status")
    result = await tool_fn()
    entry = next(s for s in result["backends"] if s["name"] == "ghost")
    assert entry["exists"] is False


# ------------------------------------------------------------------
# index_backend — mocked AgentClient
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_backend_calls_whitebeard(server: WoofServer) -> None:
    mock_result: dict[str, Any] = {"photosProcessed": 5, "errors": 0}
    mock = AsyncMock(return_value=mock_result)
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = _get_tool(server, "index_backend")
        result = await tool_fn(ctx=None, backend_name="testlib", partition="", force=False)
        assert result == mock_result
        mock.assert_called_once()
        assert mock.call_args[0][0] == "whitebeard"
        assert mock.call_args[0][1] == "index_library_tool"


@pytest.mark.asyncio
async def test_index_backend_with_partition_calls_index_partition(server: WoofServer) -> None:
    mock = AsyncMock(return_value={})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = _get_tool(server, "index_backend")
        await tool_fn(ctx=None, backend_name="testlib", partition="2024/2024-07", force=False)
        assert mock.call_args[0][1] == "index_partition_tool"
        assert mock.call_args[0][2]["partition"] == "2024/2024-07"


@pytest.mark.asyncio
async def test_index_backend_unknown_backend(server: WoofServer) -> None:
    tool_fn = _get_tool(server, "index_backend")
    with pytest.raises(ValueError, match="not found"):
        await tool_fn(ctx=None, backend_name="unknown", partition="", force=False)


# ------------------------------------------------------------------
# search_photos — mocked AgentClient
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_photos_calls_wally(server: WoofServer) -> None:
    mock_result = {"matches": [], "partitionsScanned": 3, "partitionsPruned": 1, "errors": 0}
    mock = AsyncMock(return_value=mock_result)
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = _get_tool(server, "search_photos")
        result = await tool_fn(ctx=None, backend_name="testlib", date_min="2024")
        assert result["partitionsScanned"] == 3
        assert mock.call_args[0][0] == "wally"
        assert mock.call_args[0][1] == "search_photos_tool"
        assert mock.call_args[0][2]["date_min"] == "2024"


@pytest.mark.asyncio
async def test_search_photos_omits_none_params(server: WoofServer) -> None:
    mock = AsyncMock(return_value={"matches": []})
    with patch.object(server._agent, "call_tool", new=mock):
        tool_fn = _get_tool(server, "search_photos")
        await tool_fn(ctx=None, backend_name="testlib")
        args_passed = mock.call_args[0][2]
        assert "date_min" not in args_passed
        assert "tags" not in args_passed


# ------------------------------------------------------------------
# browse_gallery
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browse_gallery_returns_resource_uri(server: WoofServer) -> None:
    tool_fn = _get_tool(server, "browse_gallery")
    matches = [{"partition": "2024/2024-07", "filename": "a.jpg", "thumbnailsPath": "x"}]
    result = await tool_fn(backend_name="testlib", matches=matches)
    assert result["_meta"]["ui"]["resourceUri"] == "ui://gallery/ouestcharlie"
    assert result["httpPort"] == 9999
    assert result["backend"] == "testlib"
    assert result["matchCount"] == 1
    # URL should be token-based: /gallery/{token}
    assert result["url"].startswith("http://127.0.0.1:9999/gallery/")
    token = result["url"].split("/gallery/")[1]
    assert len(token) > 0
    # Session should be stored
    assert token in server._gallery_sessions
    assert server._gallery_sessions[token]["matches"] == matches


@pytest.mark.asyncio
async def test_browse_gallery_unknown_backend(server: WoofServer) -> None:
    tool_fn = _get_tool(server, "browse_gallery")
    with pytest.raises(ValueError, match="not found"):
        await tool_fn(backend_name="nosuchlib", matches=[])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_tool(server: WoofServer, name: str) -> Any:
    """Extract a tool function from the FastMCP registry by name."""
    tools = server.mcp._tool_manager._tools  # type: ignore[attr-defined]
    if name not in tools:
        raise KeyError(f"Tool {name!r} not registered. Available: {list(tools)}")
    return tools[name].fn
