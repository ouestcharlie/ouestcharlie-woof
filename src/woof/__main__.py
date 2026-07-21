"""Entry point for Woof — OuEstCharlie central controller.

Woof supports two transports, selected via ``WOOF_TRANSPORT=stdio|http``
(default ``http``):

- ``http`` (default): Woof runs as a single persistent HTTP server (MCP at
  ``/mcp``, gallery + media routes alongside, one uvicorn instance). It binds
  an ephemeral loopback port, writes a discovery file
  (``woof.discovery.write_discovery``) once ready, and keeps running —
  including across multiple separate host connections — until an idle
  timeout, an authenticated ``POST /shutdown``, or a signal stops it. Hosts
  are expected to launch ``woof-bridge`` (a thin stdio↔HTTP proxy, see
  ``woof.bridge``) rather than this module directly; the bridge lazily starts
  Woof on first connection if no live instance exists yet.
- ``stdio``: the previous behavior — Woof runs directly as a stdio MCP
  server, with a *separate* gallery/media HTTP server sharing its event loop.
  Unauthenticated (relies on the host being the only thing that can spawn or
  talk to this process). Use this for the MCP Inspector workflow:

    WOOF_TRANSPORT=stdio mcp dev src/woof/__main__.py

  (``mcp dev`` drives the module-level ``mcp`` object directly over stdio,
  so ``WOOF_TRANSPORT`` must be set to ``stdio`` *before* import for its
  lifespan to start the separate gallery server the inspector expects.)

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
from woof.asgi_server import build_http_asgi_app, make_uvicorn_server
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
from woof.mcp_server import McpServer
from woof.security import allowed_hosts_for_port

_TRANSPORT = os.environ.get("WOOF_TRANSPORT", "http")

_config = WoofConfig.load()
_agent = AgentClient()
_session_manager = GallerySessionManager()

if _TRANSPORT == "stdio":
    _server = McpServer(_config, agent_client=_agent, session_manager=_session_manager)
else:
    _server = McpServer(
        _config,
        agent_client=_agent,
        session_manager=_session_manager,
        transport="http",
        token=generate_token(),
    )

mcp = _server.mcp  # module-level name required by `mcp dev`


class _ShutdownHandle:
    """Wraps the uvicorn server so ``POST /shutdown`` can trigger a clean exit."""

    def __init__(self) -> None:
        self.server: object | None = None  # uvicorn.Server, set once constructed

    def request(self) -> None:
        if self.server is not None:
            self.server.should_exit = True  # type: ignore[attr-defined]


# Idle timeout before a quiet (no bridge keepalives/requests) HTTP-mode
# instance shuts itself down. Configurable for testing.
_IDLE_TIMEOUT_SECONDS = float(os.environ.get("WOOF_IDLE_TIMEOUT_SECONDS", str(15 * 60)))
_IDLE_CHECK_INTERVAL_SECONDS = 30.0


async def _run_http() -> None:
    import asyncio

    tracker = ActivityTracker()
    handle = _ShutdownHandle()

    # Each module builds its own piece independently (FastMCP's own
    # http_app, the gallery/media routes); asgi_server.build_http_asgi_app is
    # the one place that combines and wraps them — no module reaches into
    # another's internals.
    #
    # path="/" here, NOT "/mcp": build_http_asgi_app mounts this at "/mcp",
    # which strips that prefix before dispatching. Registering this app's own
    # route at "/mcp" too would double it up (Mount strips "/mcp", leaving
    # "/", which wouldn't match an inner route still expecting "/mcp").
    assert _server._token is not None  # guaranteed by the http-mode branch above
    mcp_app = _server.mcp.http_app(path="/")
    gallery_app = build_gallery_app(
        _server._sessions,
        _server._wally_connection,
        _server.server_url,
        indexing_session_manager=_server._indexing_sessions,
        token=_server._token,
        activity_tracker=tracker,
        shutdown_handle=handle,
    )
    app = build_http_asgi_app(
        mcp_app=mcp_app,
        gallery_app=gallery_app,
        token=_server._token,
        allowed_hosts=allowed_hosts_for_port(_server._port),
        activity_tracker=tracker,
    )

    uv_server = make_uvicorn_server(app, log_level="info")
    handle.server = uv_server

    async def _signal_ready() -> None:
        while not uv_server.started:
            if uv_server.should_exit:
                return
            await asyncio.sleep(0.05)
        write_discovery(DiscoveryInfo(pid=os.getpid(), port=_server._port, token=_server._token))
        _log.info("Woof ready — http://127.0.0.1:%d", _server._port)

    def _request_idle_shutdown() -> None:
        _log.info("Idle for >%.0fs — shutting down", _IDLE_TIMEOUT_SECONDS)
        uv_server.should_exit = True

    try:
        await asyncio.gather(
            uv_server.serve(sockets=[_server._http_sock]),
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


def main() -> None:
    if _TRANSPORT == "stdio":
        mcp.run()
        return
    import asyncio

    asyncio.run(_run_http())


if __name__ == "__main__":
    main()
