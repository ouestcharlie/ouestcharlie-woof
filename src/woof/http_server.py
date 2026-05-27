"""Local HTTP server for proxying media requests and serving the gallery.

Runs on 127.0.0.1 with an OS-assigned port in a daemon thread backed by
Starlette + uvicorn (async ASGI), matching Wally's HTTP server architecture.

URL scheme:
  GET /gallery/{token}                         — gallery HTML (token identifies session)
  GET /api/results/{token}                     — JSON session data (matches + metadata)
  GET /api/results/{token}/page/{page}         — load 0-indexed Wally page into session
  GET /gallery-static/{path}                   — gallery JS/CSS assets from dist/
  GET /thumbnail/{library_name}/{partition}/{avif_hash}        — proxied to Wally
  GET /previews/{library_name}/{partition}/{content_hash}.jpg — proxied to Wally

where {partition} may contain slashes (e.g. "2024/2024-07").
All library media (thumbnails and previews) is served by Wally and proxied here.
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


def get_gallery_html(server_url: str) -> str:
    """Return the gallery HTML with asset URLs rewritten to absolute server URLs.

    Vite builds the app with base='/gallery-static/'.  At runtime we replace
    those relative-rooted paths with {server_url}/gallery-static/ so the MCP
    Apps iframe (and direct browser access) can load JS/CSS.
    """
    if _GALLERY_DIST_HTML.exists():
        html = _GALLERY_DIST_HTML.read_text(encoding="utf-8")
        return html.replace("/gallery-static/", f"{server_url}/gallery-static/")
    return _gallery_placeholder()


def start_http_server(
    session_manager: GallerySessionManager | None = None,
    wally_connection_fn: Any | None = None,
    fetch_page_fn: Any | None = None,
) -> str:
    """Start the gallery/proxy HTTP server in a daemon thread.

    Args:
        session_manager: Gallery session manager shared with WoofServer.
            Provides token-keyed session lookup for the gallery and results
            endpoints.  A new empty manager is created when omitted.
        wally_connection_fn: Callable ``(library_name: str) -> (http_port, token)``
            for the named Wally sidecar.  Called on every request so dynamic
            changes (e.g. after sidecar restart) are picked up automatically.
        fetch_page_fn: Optional synchronous callable
            ``(token: str, page: int) -> bool`` that fetches the requested
            0-indexed Wally page into the session identified by *token*.
            Called in a thread-pool executor so the HTTP event loop is not
            blocked.  Returns ``True`` on success, ``False`` on failure.

    Returns:
        The full server URL (e.g. ``"http://localhost:8080"``).
    """
    mgr = session_manager if session_manager is not None else GallerySessionManager()

    # Bind port before starting the thread so the port is known synchronously.
    # Use the loopback IP for binding but expose the URL as "localhost" so the
    # hostname matches what MCP Host writes into the iframe's CSP
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    server_url = f"http://localhost:{port}"

    app = _build_app(mgr, wally_connection_fn, server_url=server_url, fetch_page_fn=fetch_page_fn)
    ready = threading.Event()

    def _run() -> None:
        try:
            asyncio.run(_serve(app, sock, ready))
        except Exception:
            _log.exception("HTTP server thread crashed")

    threading.Thread(target=_run, daemon=True, name="woof-http").start()
    ready.wait(timeout=5.0)
    _log.info("HTTP server listening on %s", server_url)
    return server_url


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
    wally_connection_fn: Any | None,
    server_url: str,
    fetch_page_fn: Any | None = None,
) -> Any:
    """Build and return the Starlette ASGI application."""

    async def gallery_token(request: Request) -> Response:
        token = request.path_params["token"]
        if session_manager.get(token) is None:
            return Response(status_code=404)
        html = get_gallery_html(server_url)
        return HTMLResponse(
            html,
            headers={"Content-Security-Policy": f"default-src 'self' {server_url}"},
        )

    async def api_results(request: Request) -> Response:
        token = request.path_params["token"]
        session = session_manager.get(token)
        if session is None:
            return JSONResponse(
                {"error": f"Session {token!r} not found or expired"}, status_code=404
            )
        return JSONResponse(session)

    async def api_page(request: Request) -> Response:
        token = request.path_params["token"]
        try:
            page = int(request.path_params["page"])
        except (ValueError, KeyError):
            return JSONResponse({"error": "invalid page"}, status_code=400)

        session = session_manager.get(token)
        if session is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        qc = session.get("queryContext")
        if qc is None:
            return JSONResponse({"error": "no_context"}, status_code=400)

        if qc.get("page") == page:
            return JSONResponse(session)

        if fetch_page_fn is None:
            return JSONResponse({"error": "no_fetch_fn"}, status_code=503)

        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, fetch_page_fn, token, page)
        if not ok:
            return JSONResponse({"error": "fetch_failed"}, status_code=502)

        updated = session_manager.get(token)
        return JSONResponse(updated)

    async def proxy_media(request: Request) -> Response:
        kind = request.path_params["kind"]
        library = request.path_params["library"]
        rest = request.path_params["rest"]
        wally_port, wally_token = (
            wally_connection_fn(library) if wally_connection_fn is not None else (None, None)
        )
        if wally_port is None:
            return Response(
                f"Wally preview server not available for library '{library}'", status_code=503
            )
        safe = "/:@!$&'()*+,;="
        url = f"http://127.0.0.1:{wally_port}/{quote(f'{kind}/{library}/{rest}', safe=safe)}"
        headers: dict[str, str] = {}
        if wally_token:
            headers["Authorization"] = f"Bearer {wally_token}"
        async with httpx.AsyncClient() as client:
            try:
                upstream = await client.get(url, headers=headers, timeout=120.0)
            except Exception as exc:
                _log.error("Proxy to Wally failed for %r/%r/%r: %s", kind, library, rest, exc)
                return Response(status_code=503)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "image/jpeg"),
        )

    routes = [
        Route("/gallery/{token}", gallery_token),
        Route("/api/results/{token}/page/{page}", api_page),
        Route("/api/results/{token}", api_results),
        Mount("/gallery-static", StaticFiles(directory=str(_GALLERY_DIST_DIR), check_dir=False)),
        Route("/{kind}/{library}/{rest:path}", proxy_media),
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
