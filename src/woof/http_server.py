"""Local HTTP server for proxying media requests and serving the gallery.

Runs on 127.0.0.1 with an OS-assigned port in a daemon thread.  The gallery
iframe fetches images directly from this server; photo data never passes
through the MCP channel.

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

import json
import logging
import mimetypes
import queue
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

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
    gallery_sessions: "dict | None" = None,
    wally_port_fn: "Any | None" = None,
) -> int:
    """Start the gallery/proxy HTTP server in a daemon thread.

    Args:
        gallery_sessions: Shared dict for token-keyed gallery sessions.
            Keys are URL-safe tokens; values hold matches + metadata.
        wally_port_fn: Zero-argument callable returning the current Wally HTTP
            port (int or None).  Called on every request so dynamic port
            changes (e.g. after sidecar restart) are picked up automatically.

    Returns:
        The port number the server is listening on.
    """
    sessions: dict = gallery_sessions if gallery_sessions is not None else {}
    port_queue: queue.Queue[int] = queue.Queue()
    bound_port: list[int] = [0]

    def _run() -> None:
        def handler_factory(*args: object, **kwargs: object) -> _ThumbnailHandler:
            wally_port = wally_port_fn() if wally_port_fn is not None else None
            return _ThumbnailHandler(sessions, bound_port[0], wally_port, *args, **kwargs)  # type: ignore[arg-type]

        server = HTTPServer(("127.0.0.1", 0), handler_factory)
        bound_port[0] = server.server_address[1]
        port_queue.put(bound_port[0])
        _log.info("HTTP server listening on 127.0.0.1:%d", bound_port[0])
        server.serve_forever()

    thread = threading.Thread(target=_run, daemon=True, name="woof-http")
    thread.start()
    return port_queue.get(timeout=10)


class _ThumbnailHandler(BaseHTTPRequestHandler):
    """HTTP handler for gallery, media proxy, and results API."""

    def __init__(
        self,
        gallery_sessions: dict,
        http_port: int,
        wally_port: "int | None",
        *args: object,
        **kwargs: object,
    ) -> None:
        self._gallery_sessions = gallery_sessions
        self._http_port = http_port
        self._wally_port = wally_port
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path.lstrip("/"))

        if path == "gallery" or path == "":
            self._serve_gallery()
            return

        # /gallery/{token}
        if path.startswith("gallery/"):
            token = path[len("gallery/"):]
            if token in self._gallery_sessions:
                self._serve_gallery()
            else:
                self._not_found()
            return

        # /api/results/{token}
        if path.startswith("api/results/"):
            token = path[len("api/results/"):]
            self._serve_results(token)
            return

        # /gallery-static/{asset_path} — Vite-built JS/CSS assets
        if path.startswith("gallery-static/"):
            asset_path = path[len("gallery-static/"):]
            self._serve_static(asset_path)
            return

        # path = "{kind}/{backend_name}/{partition}/{filename}"
        # kind is "thumbnails" or "previews"
        parts = path.split("/", 2)
        if len(parts) < 3:
            self._not_found()
            return

        kind, backend_name, rest = parts

        # Thumbnails and previews are both served by Wally — proxy the request.
        if kind in ("thumbnails", "previews"):
            self._proxy_to_wally(path)
            return

        self._not_found()

    def _proxy_to_wally(self, path: str) -> None:
        """Proxy a media request (thumbnails or previews) to Wally's HTTP server."""
        if self._wally_port is None:
            self.send_error(503, "Wally preview server not available")
            return
        url = f"http://127.0.0.1:{self._wally_port}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            self.send_error(exc.code)
        except Exception as exc:
            _log.error("Proxy to Wally failed for %r: %s", path, exc)
            self.send_error(503)

    def _serve_static(self, asset_path: str) -> None:
        file_path = (_GALLERY_DIST_DIR / asset_path).resolve()
        # Prevent path traversal outside dist/
        try:
            file_path.relative_to(_GALLERY_DIST_DIR.resolve())
        except ValueError:
            self._not_found()
            return
        if not file_path.exists() or not file_path.is_file():
            self._not_found()
            return
        data = file_path.read_bytes()
        mime, _ = mimetypes.guess_type(str(file_path))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _serve_results(self, token: str) -> None:
        session = self._gallery_sessions.get(token)
        if session is None:
            self._json_error(404, f"Session {token!r} not found or expired")
            return
        self._json_response(session)

    def _json_response(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, status: int, message: str) -> None:
        self._json_response({"error": message}, status)

    def _serve_gallery(self) -> None:
        html = get_gallery_html(self._http_port)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Security-Policy", "default-src 'self' http://127.0.0.1:*")
        self.end_headers()
        self.wfile.write(data)

    def _not_found(self) -> None:
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        _log.debug("HTTP %s", format % args)


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
