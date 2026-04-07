"""Integration tests: start real HTTP and MCP servers, verify Whitebeard and Wally start.

Each test class covers one layer of the startup sequence that __main__.py
performs:

  1. HTTP server (daemon thread, Starlette/uvicorn)
  2. Wally sidecar (persistent subprocess, Streamable HTTP MCP)
  3. Whitebeard (ephemeral subprocess, stdio MCP)
  4. Full stack (all of the above wired together through WoofServer)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import httpx
import pytest

from woof.agent_client import AgentClient
from woof.config import BackendConfig, WoofConfig
from woof.gallery_session_manager import GallerySessionManager
from woof.http_server import start_http_server
from woof.server import WoofServer

# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class TestHttpServer:
    """The HTTP server (daemon thread) starts synchronously and responds immediately."""

    def test_binds_to_a_loopback_port(self) -> None:
        """start_http_server() should return a valid unprivileged port."""
        port = start_http_server()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_unknown_session_returns_404(self) -> None:
        """Requesting /api/results/<unknown-token> must return HTTP 404."""
        port = start_http_server()
        url = f"http://127.0.0.1:{port}/api/results/no-such-token"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(url)
        assert exc_info.value.code == 404

    def test_known_session_returns_200_with_data(self) -> None:
        """A session created in the shared manager is served via /api/results."""
        mgr = GallerySessionManager()
        token = mgr.create("integration-test", [{"filename": "a.jpg"}], http_port=0)
        port = start_http_server(session_manager=mgr)

        url = f"http://127.0.0.1:{port}/api/results/{token}"
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200
            data: dict[str, Any] = json.loads(resp.read())
        assert data["backend"] == "integration-test"
        assert data["matches"][0]["filename"] == "a.jpg"

    def test_thumbnail_without_wally_returns_503(self) -> None:
        """Media proxy returns 503 when no Wally port is configured."""
        port = start_http_server()
        url = f"http://127.0.0.1:{port}/thumbnails/lib/2024/thumbnails.avif"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(url)
        assert exc_info.value.code == 503


# ---------------------------------------------------------------------------
# Wally sidecar
# ---------------------------------------------------------------------------


class TestWallySidecar:
    """Wally starts as a persistent subprocess and exposes an MCP/HTTP endpoint."""

    @pytest.mark.asyncio
    async def test_sidecar_is_alive_after_start(
        self, agent_client: AgentClient, backend: BackendConfig
    ) -> None:
        """_get_wally_sidecar() must return a live sidecar with a valid HTTP port."""
        sidecar = await agent_client._get_wally_sidecar(backend)  # type: ignore[attr-defined]

        assert sidecar.alive, "Wally sidecar did not reach the alive state"
        assert isinstance(sidecar.http_port, int)
        assert 1024 <= sidecar.http_port <= 65535

    @pytest.mark.asyncio
    async def test_http_port_accepts_connections(
        self, agent_client: AgentClient, backend: BackendConfig
    ) -> None:
        """The HTTP port announced by Wally must accept connections."""
        sidecar = await agent_client._get_wally_sidecar(backend)  # type: ignore[attr-defined]

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"http://127.0.0.1:{sidecar.http_port}/mcp",
                    timeout=5.0,
                )
                # Any valid HTTP response (even 4xx) confirms the server is up.
                assert resp.status_code < 600
            except httpx.ConnectError as exc:
                pytest.fail(f"Could not connect to Wally HTTP port {sidecar.http_port}: {exc}")

    @pytest.mark.asyncio
    async def test_get_wally_http_port_returns_none_before_start(
        self, agent_client: AgentClient, backend: BackendConfig
    ) -> None:
        """get_wally_http_port() returns None before the sidecar is started."""
        assert agent_client.get_wally_http_port(backend.name) is None

    @pytest.mark.asyncio
    async def test_get_wally_http_port_returns_port_after_start(
        self, agent_client: AgentClient, backend: BackendConfig
    ) -> None:
        """get_wally_http_port() returns the live port after the sidecar has started."""
        await agent_client._get_wally_sidecar(backend)  # type: ignore[attr-defined]

        port = agent_client.get_wally_http_port(backend.name)
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    @pytest.mark.asyncio
    async def test_second_call_reuses_sidecar(
        self, agent_client: AgentClient, backend: BackendConfig
    ) -> None:
        """Calling _get_wally_sidecar() twice must return the same running sidecar."""
        sidecar_a = await agent_client._get_wally_sidecar(backend)  # type: ignore[attr-defined]
        sidecar_b = await agent_client._get_wally_sidecar(backend)  # type: ignore[attr-defined]

        assert sidecar_a is sidecar_b, "A new sidecar was created on the second call"


# ---------------------------------------------------------------------------
# Whitebeard (ephemeral)
# ---------------------------------------------------------------------------


class TestWhitebeard:
    """Whitebeard spawns as a fresh subprocess per call and returns a result dict."""

    @pytest.mark.asyncio
    async def test_indexes_empty_library_without_error(
        self, agent_client: AgentClient, backend: BackendConfig
    ) -> None:
        """Whitebeard should start, index an empty directory, and return a result dict."""
        result = await agent_client.call_tool(
            "whitebeard",
            "index_library",
            {"force_extract_exif": False, "generate_thumbnails": False},
            backend,
        )
        assert isinstance(result, dict), f"Expected dict, got {result!r}"

    @pytest.mark.asyncio
    async def test_result_contains_photos_processed(
        self, agent_client: AgentClient, backend: BackendConfig
    ) -> None:
        """The result from Whitebeard must include a photosProcessed count."""
        result = await agent_client.call_tool(
            "whitebeard",
            "index_library",
            {"force_extract_exif": False, "generate_thumbnails": False},
            backend,
        )
        assert "totalPhotos" in result, (
            f"Expected 'totalPhotos' key in result, got keys: {list(result)}"
        )
        assert result["totalPhotos"] == 0  # empty directory


# ---------------------------------------------------------------------------
# Full stack
# ---------------------------------------------------------------------------


class TestFullStack:
    """Reproduce the __main__.py startup sequence end-to-end."""

    @pytest.mark.asyncio
    async def test_woof_server_triggers_wally_on_list_search_fields(
        self, config: WoofConfig
    ) -> None:
        """list_search_fields called on WoofServer must start the Wally sidecar."""
        agent = AgentClient()
        try:
            session_manager = GallerySessionManager()
            http_port = start_http_server(session_manager=session_manager)
            server = WoofServer(
                config,
                http_port=http_port,
                agent_client=agent,
                session_manager=session_manager,
            )

            tool = await server.mcp.get_tool("list_search_fields")
            await tool.fn(backend_name="integration-test")

            wally_port = agent.get_wally_http_port("integration-test")
            assert isinstance(wally_port, int), (
                "Wally did not start: get_wally_http_port() returned None after "
                "list_search_fields was called"
            )
        finally:
            await agent.shutdown()

    @pytest.mark.asyncio
    async def test_woof_server_triggers_wally_on_get_partition_summaries(
        self, config: WoofConfig
    ) -> None:
        """get_partition_summaries called on WoofServer must start the Wally sidecar."""
        agent = AgentClient()
        try:
            session_manager = GallerySessionManager()
            http_port = start_http_server(session_manager=session_manager)
            server = WoofServer(
                config,
                http_port=http_port,
                agent_client=agent,
                session_manager=session_manager,
            )

            tool = await server.mcp.get_tool("get_partition_summaries")
            result = await tool.fn()

            # Wally must have started
            wally_port = agent.get_wally_http_port("integration-test")
            assert isinstance(wally_port, int), (
                "Wally did not start: get_wally_http_port() returned None after "
                "get_partition_summaries was called"
            )
            # Result is a list of backend summaries
            assert isinstance(result, list)
            assert any(s.get("name") == "integration-test" for s in result)
        finally:
            await agent.shutdown()

    @pytest.mark.asyncio
    async def test_http_server_proxies_to_wally_after_startup(self, config: WoofConfig) -> None:
        """After Wally starts, the Woof HTTP proxy can reach Wally (not 503)."""
        agent = AgentClient()
        try:
            session_manager = GallerySessionManager()

            def _wally_port_fn() -> int | None:
                if not config.backends:
                    return None
                return agent.get_wally_http_port(config.backends[0].name)

            def _wally_token_fn() -> str | None:
                if not config.backends:
                    return None
                return agent.get_wally_token(config.backends[0].name)

            http_port = start_http_server(
                session_manager=session_manager,
                wally_port_fn=_wally_port_fn,
                wally_token_fn=_wally_token_fn,
            )
            server = WoofServer(
                config,
                http_port=http_port,
                agent_client=agent,
                session_manager=session_manager,
            )

            # Trigger Wally startup
            tool = await server.mcp.get_tool("list_search_fields")
            await tool.fn(backend_name="integration-test")

            # The proxy now knows Wally's port: a thumbnail request should reach
            # Wally and return 404 (unknown partition), not 503 (no Wally port).
            url = f"http://127.0.0.1:{http_port}/thumbnails/integration-test/2024/thumbnails.avif"
            try:
                urllib.request.urlopen(url)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404, (
                    f"Expected 404 from Wally via proxy, got {exc.code}. "
                    "A 503 means the proxy could not reach Wally."
                )
        finally:
            await agent.shutdown()
