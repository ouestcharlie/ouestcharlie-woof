"""Test-only helper: run the gallery/media HTTP app standalone, with no MCP app.

Production always serves ``build_gallery_app``'s app mounted alongside the MCP
app in one combined app (see ``woof.asgi_server.build_http_asgi_app``, driven
by ``woof.__main__``). Tests that only need the gallery/media HTTP surface in
isolation use this instead, in a daemon thread with its own event loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from woof.asgi_server import bind_loopback_endpoint, serve_with_ready, with_permissive_cors
from woof.gallery_session_manager import GallerySessionManager
from woof.http_server import build_gallery_app

_log = logging.getLogger(__name__)


def start_http_server(
    session_manager: GallerySessionManager | None = None,
    wally_connection_fn: Any | None = None,
    indexing_session_manager: Any | None = None,
) -> str:
    """Start the gallery/proxy HTTP server (gallery routes only, no MCP app) in a daemon thread.

    Args:
        session_manager: Gallery session manager shared with McpServer.
        wally_connection_fn: Callable ``(library_name: str) -> (http_port, token)``
            for the named Wally sidecar.

    Returns:
        The full server URL (e.g. ``"http://localhost:8080"``).
    """
    mgr = session_manager if session_manager is not None else GallerySessionManager()

    # Bind port before starting the thread so the port is known synchronously.
    # Use the loopback IP for binding but expose the URL as "localhost" so the
    # hostname matches what MCP Host writes into the iframe's CSP.
    endpoint = bind_loopback_endpoint()
    server_url = f"http://localhost:{endpoint.port}"

    app = with_permissive_cors(
        build_gallery_app(
            mgr,
            wally_connection_fn,
            server_url=server_url,
            indexing_session_manager=indexing_session_manager,
        )
    )
    ready = threading.Event()

    def _run() -> None:
        try:
            asyncio.run(serve_with_ready(app, endpoint, ready))
        except Exception:
            _log.exception("HTTP server thread crashed")

    threading.Thread(target=_run, daemon=True, name="woof-http").start()
    ready.wait(timeout=5.0)
    _log.info("HTTP server listening on %s", server_url)
    return server_url
