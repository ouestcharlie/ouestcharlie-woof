"""Local HTTP server for proxying media requests and serving the gallery.

Runs on 127.0.0.1 with an OS-assigned port in a daemon thread backed by
Starlette + uvicorn (async ASGI), matching Wally's HTTP server architecture.

URL scheme:
  GET /gallery/{token}                         — gallery HTML (token identifies session)
  GET /api/results/{token}                     — JSON session data (matches + metadata)
  GET /gallery-static/{path}                   — gallery JS/CSS assets from dist/
  GET /thumbnails/{backend_name}/{partition}/thumbnails.avif  — proxied to Wally
  GET /previews/{backend_name}/{partition}/{content_hash}.jpg — proxied to Wally

where {partition} may contain slashes (e.g. "2024/2024-07").
All backend media (thumbnails and previews) is served by Wally and proxied here.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .gallery_session_manager import GallerySessionManager

_log = logging.getLogger(__name__)

# Pre-built Svelte app (produced by `npm run build` in gallery/)
_GALLERY_DIST_DIR = Path(__file__).parent / "gallery" / "dist"
_GALLERY_DIST_HTML = _GALLERY_DIST_DIR / "index.html"


def get_gallery_html(http_port: int) -> str:
    """Return the gallery HTML with asset URLs rewritten to absolute localhost URLs.

    Vite builds the app with base='/gallery-static/'.  At runtime we replace
    those relative-rooted paths with http://127.0.0.1:{http_port}/gallery-static/
    so the MCP Apps iframe (and direct browser access) can load JS/CSS.
    """
    if _GALLERY_DIST_HTML.exists():
        html = _GALLERY_DIST_HTML.read_text(encoding="utf-8")
        base = f"http://127.0.0.1:{http_port}/gallery-static/"
        return html.replace("/gallery-static/", base)
    return _gallery_placeholder()


def start_http_server(
    session_manager: GallerySessionManager | None = None,
    wally_port_fn: Any | None = None,
    wally_token_fn: Any | None = None,
) -> int:
    """Start the gallery/proxy HTTP server in a daemon thread.

    Args:
        session_manager: Gallery session manager shared with WoofServer.
            Provides token-keyed session lookup for the gallery and results
            endpoints.  A new empty manager is created when omitted.
        wally_port_fn: Zero-argument callable returning the current Wally HTTP
            port (int or None).  Called on every request so dynamic port
            changes (e.g. after sidecar restart) are picked up automatically.
        wally_token_fn: Zero-argument callable returning the current Wally Bearer
            token (str or None).  Forwarded as Authorization header on proxy requests.

    Returns:
        The port number the server is listening on.
    """
    mgr = session_manager if session_manager is not None else GallerySessionManager()

    # Bind port before starting the thread so the port is known synchronously.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]

    app = _build_app(mgr, wally_port_fn, wally_token_fn, http_port=port)
    ready = threading.Event()

    def _run() -> None:
        try:
            asyncio.run(_serve(app, sock, ready))
        except Exception:
            _log.exception("HTTP server thread crashed")

    threading.Thread(target=_run, daemon=True, name="woof-http").start()
    ready.wait(timeout=5.0)
    _log.info("HTTP server listening on 127.0.0.1:%d", port)
    return port


async def _serve(app: Any, sock: socket.socket, ready: threading.Event) -> None:
    import uvicorn

    class _Server(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass  # Signal handling belongs to the main thread; no-op in daemon thread

        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            ready.set()

    config = uvicorn.Config(app, log_level="warning", access_log=False)
    server = _Server(config)
    await server.serve(sockets=[sock])


def _build_app(
    session_manager: GallerySessionManager,
    wally_port_fn: Any | None,
    wally_token_fn: Any | None,
    http_port: int,
) -> Any:
    """Build and return the Starlette ASGI application."""

    async def gallery_token(request: Request) -> Response:
        token = request.path_params["token"]
        if session_manager.get(token) is None:
            return Response(status_code=404)
        html = get_gallery_html(http_port)
        return HTMLResponse(
            html,
            headers={"Content-Security-Policy": "default-src 'self' http://127.0.0.1:*"},
        )

    async def api_results(request: Request) -> Response:
        token = request.path_params["token"]
        session = session_manager.get(token)
        if session is None:
            return JSONResponse(
                {"error": f"Session {token!r} not found or expired"}, status_code=404
            )
        return JSONResponse(session)

    async def proxy_media(request: Request) -> Response:
        wally_port = wally_port_fn() if wally_port_fn is not None else None
        if wally_port is None:
            return Response("Wally preview server not available", status_code=503)
        kind = request.path_params["kind"]
        rest = request.path_params["rest"]
        safe = "/:@!$&'()*+,;="
        url = f"http://127.0.0.1:{wally_port}/{quote(f'{kind}/{rest}', safe=safe)}"
        headers: dict[str, str] = {}
        wally_token = wally_token_fn() if wally_token_fn is not None else None
        if wally_token:
            headers["Authorization"] = f"Bearer {wally_token}"
        async with httpx.AsyncClient() as client:
            try:
                upstream = await client.get(url, headers=headers, timeout=120.0)
            except Exception as exc:
                _log.error("Proxy to Wally failed for %r/%r: %s", kind, rest, exc)
                return Response(status_code=503)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "image/jpeg"),
        )

    routes = [
        Route("/gallery/{token}", gallery_token),
        Route("/api/results/{token}", api_results),
        Mount("/gallery-static", StaticFiles(directory=str(_GALLERY_DIST_DIR), check_dir=False)),
        Route("/{kind}/{rest:path}", proxy_media),
    ]
    app = Starlette(routes=routes)
    return CORSMiddleware(app, allow_origins=["*"])


def _gallery_placeholder() -> str:
    """Placeholder served when the gallery bundle has not been built yet."""
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
        "<title>OuEstCharlie Gallery</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#111;color:#888;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
        "</style></head><body>"
        "<p>Gallery not built. Run <code>npm run build</code> in <code>gallery/</code>.</p>"
        "</body></html>"
    )
