"""Local HTTP server for serving thumbnail and preview AVIF containers.

Runs on 127.0.0.1 with an OS-assigned port in a daemon thread.  The gallery
iframe fetches images directly from this server; photo data never passes
through the MCP channel.

URL scheme:
  GET /gallery/{token}                   — gallery HTML (token identifies session)
  GET /api/results/{token}               — JSON session data (matches + metadata)
  GET /thumbnails/{backend_name}/{partition}/thumbnails.avif
  GET /previews/{backend_name}/{partition}/previews.avif

where {partition} may contain slashes (e.g. "2024/2024-07").
The corresponding file on disk is:
  {backend.path}/{partition}/.ouestcharlie/thumbnails.avif
  {backend.path}/{partition}/.ouestcharlie/previews.avif
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from .config import WoofConfig

_log = logging.getLogger(__name__)

# Pre-built Svelte bundle (produced by `npm run build` in gallery/)
_GALLERY_DIST = Path(__file__).parent / "gallery" / "dist" / "index.html"

# File extension → AVIF filename inside .ouestcharlie/
_SUFFIX_MAP = {
    "thumbnails.avif": "thumbnails.avif",
    "previews.avif": "previews.avif",
}


def get_gallery_html() -> str:
    """Return the gallery HTML — built bundle if present, placeholder otherwise."""
    if _GALLERY_DIST.exists():
        return _GALLERY_DIST.read_text(encoding="utf-8")
    return _gallery_placeholder()


def start_http_server(
    config: "WoofConfig",
    gallery_sessions: "dict | None" = None,
) -> int:
    """Start the thumbnail/gallery HTTP server in a daemon thread.

    Args:
        config: WoofConfig — used to resolve backend paths.
        gallery_sessions: Shared dict for token-keyed gallery sessions.
            Keys are URL-safe tokens; values hold matches + metadata.

    Returns:
        The port number the server is listening on.
    """
    sessions: dict = gallery_sessions if gallery_sessions is not None else {}
    port_queue: queue.Queue[int] = queue.Queue()

    def _run() -> None:
        def handler_factory(*args: object, **kwargs: object) -> _ThumbnailHandler:
            return _ThumbnailHandler(config, sessions, *args, **kwargs)  # type: ignore[arg-type]

        server = HTTPServer(("127.0.0.1", 0), handler_factory)
        port_queue.put(server.server_address[1])
        _log.info("HTTP server listening on 127.0.0.1:%d", server.server_address[1])
        server.serve_forever()

    thread = threading.Thread(target=_run, daemon=True, name="woof-http")
    thread.start()
    return port_queue.get(timeout=10)


class _ThumbnailHandler(BaseHTTPRequestHandler):
    """HTTP handler for gallery, thumbnails, previews, and results API."""

    def __init__(
        self,
        config: "WoofConfig",
        gallery_sessions: dict,
        *args: object,
        **kwargs: object,
    ) -> None:
        self._config = config
        self._gallery_sessions = gallery_sessions
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

        # path = "{kind}/{backend_name}/{partition}/{filename}"
        # kind is "thumbnails" or "previews"
        parts = path.split("/", 2)
        if len(parts) < 3:
            self._not_found()
            return

        _kind, backend_name, rest = parts
        # rest = "{partition}/{filename}" — filename is the last segment
        rest_parts = rest.rsplit("/", 1)
        if len(rest_parts) != 2:
            self._not_found()
            return

        partition, filename = rest_parts
        if filename not in _SUFFIX_MAP:
            self._not_found()
            return

        backend = self._config.get_backend(backend_name)
        if backend is None:
            _log.warning("Unknown backend %r in HTTP request", backend_name)
            self._not_found()
            return

        file_path = Path(backend.path) / partition / ".ouestcharlie" / _SUFFIX_MAP[filename]
        if not file_path.exists():
            _log.debug("File not found: %s", file_path)
            self._not_found()
            return

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/avif")
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
        html = get_gallery_html()
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

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        _log.debug("HTTP %s", fmt % args)


def _gallery_placeholder() -> str:
    """Minimal HTML served when no built gallery bundle is present.

    Extracts the session token from the URL path (/gallery/{token}),
    fetches /api/results/{token} to get pre-computed matches, and renders
    the photo grid.  No search form — Claude runs search_photos and passes
    results to browse_gallery.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OuEstCharlie Gallery</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; background: #111; color: #eee; }
    #app { display: flex; flex-direction: column; height: 100vh; }
    header { padding: 1rem; background: #1a1a1a; border-bottom: 1px solid #333; }
    header h1 { margin: 0; font-size: 1.1rem; }
    #grid { flex: 1; overflow-y: auto; display: flex; flex-wrap: wrap; gap: 4px; padding: 1rem; align-content: flex-start; }
    .tile { width: 160px; height: 160px; overflow: hidden; cursor: pointer; border-radius: 4px; background: #222; flex-shrink: 0; position: relative; }
    .tile img { display: block; }
    #status { padding: 0.5rem 1rem; font-size: 0.8rem; color: #888; background: #1a1a1a; border-top: 1px solid #333; }
    #preview { position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: none; align-items: center; justify-content: center; flex-direction: column; gap: 0.75rem; }
    #preview.open { display: flex; }
    .preview-clip { overflow: hidden; border-radius: 4px; }
    .preview-clip img { display: block; }
    #preview-close { position: absolute; top: 1rem; right: 1rem; background: #333; border: none; color: #eee; padding: 0.4rem 0.8rem; cursor: pointer; border-radius: 4px; }
  </style>
</head>
<body>
<div id="app">
  <header><h1>OuEstCharlie</h1></header>
  <div id="grid"></div>
  <div id="status">Loading\u2026</div>
</div>
<div id="preview">
  <button id="preview-close">Close</button>
  <div class="preview-clip" id="preview-clip"></div>
</div>

<script>
  const DISPLAY_SIZE = 160; // px per thumbnail tile
  let matches = [];

  const token = location.pathname.split('/').pop();
  const httpPort = location.port ? parseInt(location.port) : 80;

  // Returns tile geometry {url, col, row, tileSize, cols} or null.
  function tileInfo(match, isThumb, backend) {
    var path, cols, tileSize, avifKind, avifFile;
    if (isThumb) {
      path = match.thumbnailsPath;
      cols = match.thumbnailCols;
      tileSize = match.thumbnailTileSize;
      avifKind = 'thumbnails';
      avifFile = 'thumbnails.avif';
    } else {
      path = match.previewsPath;
      cols = match.previewCols;
      tileSize = match.previewTileSize;
      avifKind = 'previews';
      avifFile = 'previews.avif';
    }
    if (!path || match.tileIndex == null || !cols || !tileSize) { return null; }
    var partition = encodeURIComponent(match.partition).replace(/%2F/g, '/');
    var url = 'http://127.0.0.1:' + httpPort + '/' + avifKind + '/' + backend + '/' + partition + '/' + avifFile;
    return { url: url, col: match.tileIndex % cols, row: Math.floor(match.tileIndex / cols), tileSize: tileSize, cols: cols };
  }

  // Creates an <img> that shows the correct tile from the AVIF grid at displaySize px.
  function makeTileImg(tile, displaySize) {
    var img = document.createElement('img');
    img.src = tile.url;
    img.style.width = (tile.cols * displaySize) + 'px';
    img.style.height = 'auto';
    img.style.marginLeft = -(tile.col * displaySize) + 'px';
    img.style.marginTop = -(tile.row * displaySize) + 'px';
    img.style.display = 'block';
    return img;
  }

  function renderGrid(newMatches, backend) {
    matches = newMatches;
    var grid = document.getElementById('grid');
    grid.innerHTML = '';
    for (var i = 0; i < matches.length; i++) {
      var match = matches[i];
      var tile = tileInfo(match, true, backend);
      var div = document.createElement('div');
      div.className = 'tile';
      div.title = match.filename;
      div.addEventListener('click', (function(idx) {
        return function() { openPreview(idx, backend); };
      })(i));
      if (tile) { div.appendChild(makeTileImg(tile, DISPLAY_SIZE)); }
      grid.appendChild(div);
    }
  }

  function openPreview(i, backend) {
    var match = matches[i];
    var tile = tileInfo(match, false, backend);
    if (!tile) { tile = tileInfo(match, true, backend); }
    var clip = document.getElementById('preview-clip');
    clip.innerHTML = '';
    if (!tile) { return; }
    var displaySize = Math.min(tile.tileSize, Math.min(window.innerWidth * 0.85, window.innerHeight * 0.82));
    clip.style.width = displaySize + 'px';
    clip.style.height = displaySize + 'px';
    clip.appendChild(makeTileImg(tile, displaySize));
    document.getElementById('preview').classList.add('open');
  }

  document.getElementById('preview-close').addEventListener('click', function() {
    document.getElementById('preview').classList.remove('open');
  });

  async function loadResults() {
    const status = document.getElementById('status');
    try {
      const resp = await fetch('http://127.0.0.1:' + httpPort + '/api/results/' + token);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: resp.statusText }));
        status.textContent = 'Error: ' + (err.error || resp.statusText);
        return;
      }
      const session = await resp.json();
      const m = session.matches || [];
      renderGrid(m, session.backend);
      status.textContent = m.length + ' photo' + (m.length === 1 ? '' : 's');
    } catch (err) {
      status.textContent = 'Error: ' + err.message;
    }
  }

  loadResults();
</script>
</body>
</html>"""
