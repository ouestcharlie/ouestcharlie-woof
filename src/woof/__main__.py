"""Entry point for Woof — OuEstCharlie central controller.

Woof runs as a single persistent HTTP server (MCP at ``/mcp``, gallery +
media routes alongside, one uvicorn instance). It binds an ephemeral
loopback port, writes a discovery file (``woof.discovery.write_discovery``)
once ready, and keeps running — including across multiple separate host
connections — until an idle timeout, an authenticated ``POST /shutdown``, or
a signal stops it. Hosts are expected to launch ``woof-bridge`` (a thin
stdio↔HTTP proxy, see ``woof.bridge``) rather than this module directly; the
bridge lazily starts Woof on first connection if no live instance exists yet.

For local development with the MCP Inspector, point it at the bridge rather
than this module directly (see README_DEV.md):

    npx @modelcontextprotocol/inspector .venv/bin/woof-bridge

Logs are written to ~/Library/Logs/ouestcharlie/woof.log (macOS) or to
WOOF_LOG_FILE if set.
"""

from __future__ import annotations

import logging
import os

from ouestcharlie_toolkit import setup_logging

_log_file = setup_logging("woof", log_file_env_var="WOOF_LOG_FILE", level=logging.DEBUG)
_log = logging.getLogger(__name__)
_log.info("Woof starting — log: %s", _log_file)

from woof.agent_client import AgentClient
from woof.asgi_server import bind_loopback_endpoint, build_http_asgi_app, make_uvicorn_server
from woof.config import WoofConfig
from woof.discovery import (
    ActivityTracker,
    DiscoveryInfo,
    generate_token,
    remove_discovery,
    watch_idle,
    write_discovery,
)
from woof.gallery_session_manager import GallerySessionManager
from woof.http_server import build_gallery_app
from woof.indexing_session_manager import IndexingSessionManager
from woof.mcp_server import McpServer

_token = generate_token()
_config = WoofConfig.load()
_agent_client = AgentClient()
_gallery_session_manager = GallerySessionManager()
_indexing_session_manager = IndexingSessionManager()

# Bind socket to get a loopback (local) URL
_endpoint = bind_loopback_endpoint()

_mcp_server = McpServer(
    _config,
    server_urls=_endpoint.urls,
    agent_client=_agent_client,
    session_manager=_gallery_session_manager,
    indexing_session_manager=_indexing_session_manager,
    token=_token,
)


class ShutdownHandle:
    """Wraps the uvicorn server so ``POST /shutdown`` can trigger a clean exit."""

    def __init__(self) -> None:
        self.server: object | None = None  # uvicorn.Server, set once constructed

    def request(self) -> None:
        if self.server is not None:
            self.server.should_exit = True  # type: ignore[attr-defined]


# Idle timeout before a quiet (no bridge keepalives/requests) instance shuts
# itself down. Configurable for testing.
_IDLE_TIMEOUT_SECONDS = float(os.environ.get("WOOF_IDLE_TIMEOUT_SECONDS", str(15 * 60)))
_IDLE_CHECK_INTERVAL_SECONDS = 5.0


async def _run() -> None:
    import asyncio

    tracker = ActivityTracker()
    handle = ShutdownHandle()

    # Each module builds its own piece independently (FastMCP's own
    # http_app, the gallery/media routes); asgi_server.build_http_asgi_app is
    # the one place that combines and wraps them — no module reaches into
    # another's internals.
    #
    # path="/" here, NOT "/mcp": build_http_asgi_app mounts this at "/mcp",
    # which strips that prefix before dispatching. Registering this app's own
    # route at "/mcp" too would double it up (Mount strips "/mcp", leaving
    # "/", which wouldn't match an inner route still expecting "/mcp").
    assert _token is not None
    mcp_app = _mcp_server.mcp.http_app(path="/")
    gallery_app = build_gallery_app(
        _gallery_session_manager,
        _agent_client.get_wally_connection,
        _endpoint.url,
        indexing_session_manager=_indexing_session_manager,
        token=_token,
        activity_tracker=tracker,
        shutdown_handle=handle,
    )
    app = build_http_asgi_app(
        mcp_app=mcp_app,
        gallery_app=gallery_app,
        token=_token,
        allowed_hosts=_endpoint.allowed_hosts,
        activity_tracker=tracker,
    )

    uv_server = make_uvicorn_server(app, _endpoint, log_level="info")
    handle.server = uv_server

    async def _signal_ready() -> None:
        while not uv_server.started:
            if uv_server.should_exit:
                return
            await asyncio.sleep(0.05)
        write_discovery(DiscoveryInfo(pid=os.getpid(), port=_endpoint.port, token=_token))
        _log.info("Woof ready — http://127.0.0.1:%d", _endpoint.port)

    def _request_idle_shutdown() -> None:
        _log.info("Idle for >%.0fs — shutting down", _IDLE_TIMEOUT_SECONDS)
        uv_server.should_exit = True

    try:
        await asyncio.gather(
            uv_server.serve(),
            _signal_ready(),
            watch_idle(
                tracker,
                idle_timeout=_IDLE_TIMEOUT_SECONDS,
                check_interval=_IDLE_CHECK_INTERVAL_SECONDS,
                should_exit=lambda: uv_server.should_exit,
                request_shutdown=_request_idle_shutdown,
            ),
        )
    finally:
        remove_discovery()
        await _agent_client.shutdown()


def main() -> None:
    import asyncio

    asyncio.run(_run())


if __name__ == "__main__":
    main()
