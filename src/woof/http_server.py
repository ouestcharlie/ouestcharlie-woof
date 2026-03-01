"""Local HTTP server for serving thumbnail and preview AVIF containers.

Runs on 127.0.0.1 with an OS-assigned port in a daemon thread.  The gallery
iframe fetches images directly from this server; photo data never passes
through the MCP channel.

URL scheme:
  GET /gallery[?backend={name}]          — gallery HTML (standalone browser)
  GET /thumbnails/{backend_name}/{partition}/thumbnails.avif
  GET /previews/{backend_name}/{partition}/previews.avif

where {partition} may contain slashes (e.g. "2024/2024-07").
The corresponding file on disk is:
  {backend.path}/{partition}/.ouestcharlie/thumbnails.avif
  {backend.path}/{partition}/.ouestcharlie/previews.avif
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

if TYPE_CHECKING:
    from .agent_client import AgentClient
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
    agent_client: "AgentClient | None" = None,
) -> int:
    """Start the thumbnail/gallery HTTP server in a daemon thread.

    Args:
        config: WoofConfig — used to resolve backend paths.
        agent_client: Optional AgentClient used to serve /api/search requests.
            If omitted, search requests return 503.

    Returns:
        The port number the server is listening on.
    """
    port_queue: queue.Queue[int] = queue.Queue()

    def _run() -> None:
        def handler_factory(*args: object, **kwargs: object) -> _ThumbnailHandler:
            return _ThumbnailHandler(config, agent_client, *args, **kwargs)  # type: ignore[arg-type]

        server = HTTPServer(("127.0.0.1", 0), handler_factory)
        port_queue.put(server.server_address[1])
        _log.info("HTTP server listening on 127.0.0.1:%d", server.server_address[1])
        server.serve_forever()

    thread = threading.Thread(target=_run, daemon=True, name="woof-http")
    thread.start()
    return port_queue.get(timeout=10)


class _ThumbnailHandler(BaseHTTPRequestHandler):
    """HTTP handler for gallery, thumbnails, previews, and search API."""

    def __init__(
        self,
        config: "WoofConfig",
        agent_client: "AgentClient | None",
        *args: object,
        **kwargs: object,
    ) -> None:
        self._config = config
        self._agent_client = agent_client
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path.lstrip("/"))

        if path == "gallery" or path == "":
            self._serve_gallery()
            return

        if path == "api/search":
            self._serve_search(parsed.query)
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

    def _serve_search(self, query_string: str) -> None:
        if self._agent_client is None:
            self._json_error(503, "Search agent not available")
            return

        params = parse_qs(query_string)

        def _get(key: str) -> str | None:
            vals = params.get(key)
            return vals[0] if vals else None

        backend_name = _get("backend")
        if not backend_name:
            self._json_error(400, "Missing required parameter: backend")
            return

        backend = self._config.get_backend(backend_name)
        if backend is None:
            self._json_error(404, f"Backend {backend_name!r} not found")
            return

        args: dict[str, Any] = {}
        for key in ("date_min", "date_max", "make", "model"):
            val = _get(key)
            if val:
                args[key] = val
        for key in ("rating_min", "rating_max"):
            val = _get(key)
            if val is not None:
                try:
                    args[key] = int(val)
                except ValueError:
                    pass
        tags_str = _get("tags")
        if tags_str:
            args["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]

        try:
            result = asyncio.run(
                self._agent_client.call_tool("wally", "search_photos_tool", args, backend)
            )
        except Exception as exc:
            _log.exception("Search agent error")
            self._json_error(500, str(exc))
            return

        self._json_response(result)

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
        self.end_headers()
        self.wfile.write(data)

    def _not_found(self) -> None:
        self.send_error(404)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        _log.debug("HTTP %s", fmt % args)


def _gallery_placeholder() -> str:
    """Minimal HTML served when no built gallery bundle is present.

    Reads httpPort from location.port and backend from ?backend= query param,
    enabling direct browser access via the URL returned by browse_gallery.
    Full search functionality requires the MCP App iframe context (Claude Desktop).
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
    #search { display: flex; gap: 0.5rem; padding: 1rem; background: #1a1a1a; }
    #search input { flex: 1; padding: 0.4rem; background: #222; border: 1px solid #444; color: #eee; border-radius: 4px; }
    #search button { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
    #grid { flex: 1; overflow-y: auto; display: flex; flex-wrap: wrap; gap: 4px; padding: 1rem; align-content: flex-start; }
    .tile { width: 160px; height: 160px; object-fit: cover; cursor: pointer; border-radius: 4px; background: #222; }
    #status { padding: 0.5rem 1rem; font-size: 0.8rem; color: #888; background: #1a1a1a; border-top: 1px solid #333; }
    #preview { position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: none; align-items: center; justify-content: center; }
    #preview.open { display: flex; }
    #preview img { max-width: 90vw; max-height: 90vh; object-fit: contain; }
    #preview-close { position: absolute; top: 1rem; right: 1rem; background: #333; border: none; color: #eee; padding: 0.4rem 0.8rem; cursor: pointer; border-radius: 4px; }
  </style>
</head>
<body>
<div id="app">
  <header><h1>OuEstCharlie</h1></header>
  <div id="search">
    <input id="date-min" type="text" placeholder="Date from (e.g. 2024-07)" />
    <input id="date-max" type="text" placeholder="Date to" />
    <input id="tags" type="text" placeholder="Tags (comma-separated)" />
    <button id="btn-search">Search</button>
  </div>
  <div id="grid"></div>
  <div id="status">Initialising…</div>
</div>
<div id="preview">
  <button id="preview-close">Close</button>
  <img id="preview-img" src="" alt="Preview" />
</div>

<script>
  let httpPort = null;
  let backendName = null;
  let matches = [];
  const callId = () => Math.random().toString(36).slice(2);
  const pending = {};

  // Detect whether the gallery is opened standalone in a browser (URL params)
  // or embedded in Claude Desktop as an MCP App iframe (ui/initialize message).
  const _urlParams = new URLSearchParams(location.search);
  const _standalone = !!_urlParams.get('backend');

  // Initialise from URL query params (standalone browser mode)
  if (_standalone) {
    httpPort = location.port ? parseInt(location.port) : (location.protocol === 'https:' ? 443 : 80);
    backendName = _urlParams.get('backend');
    document.querySelector('#status').textContent = 'Backend: ' + backendName;
  }

  // HTTP search — used in standalone mode (calls /api/search directly)
  async function httpSearch(args) {
    const p = new URLSearchParams({ backend: args.backend_name });
    if (args.date_min) p.set('date_min', args.date_min);
    if (args.date_max) p.set('date_max', args.date_max);
    if (args.tags && args.tags.length) p.set('tags', args.tags.join(','));
    if (args.rating_min != null) p.set('rating_min', args.rating_min);
    if (args.rating_max != null) p.set('rating_max', args.rating_max);
    if (args.make) p.set('make', args.make);
    if (args.model) p.set('model', args.model);
    const resp = await fetch('http://127.0.0.1:' + httpPort + '/api/search?' + p);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      throw new Error(err.error || resp.statusText);
    }
    return resp.json();
  }

  // MCP App bridge — used when running inside the Claude Desktop iframe
  function callTool(tool, args) {
    return new Promise((resolve, reject) => {
      const id = callId();
      pending[id] = { resolve, reject };
      window.parent.postMessage({ type: 'mcp_invoke', id, tool, args }, '*');
      setTimeout(() => {
        if (pending[id]) {
          delete pending[id];
          reject(new Error('Tool call timed out: ' + tool));
        }
      }, 120000);
    });
  }

  window.addEventListener('message', (e) => {
    const d = e.data;
    if (d.type === 'ui/initialize') {
      httpPort = d.httpPort;
      backendName = d.backend;
      document.querySelector('#status').textContent = 'Backend: ' + backendName;
    }
    if (d.type === 'mcp_result' && pending[d.id]) {
      const { resolve, reject } = pending[d.id];
      delete pending[d.id];
      d.error ? reject(new Error(d.error)) : resolve(d.result);
    }
  });

  function thumbUrl(match) {
    if (!httpPort || !match.thumbnailsPath) return null;
    const partition = encodeURIComponent(match.partition).replace(/%2F/g, '/');
    return 'http://127.0.0.1:' + httpPort + '/thumbnails/' + backendName + '/' + partition + '/thumbnails.avif';
  }

  function previewUrl(match) {
    if (!httpPort || !match.previewsPath) return null;
    const partition = encodeURIComponent(match.partition).replace(/%2F/g, '/');
    return 'http://127.0.0.1:' + httpPort + '/previews/' + backendName + '/' + partition + '/previews.avif';
  }

  function renderGrid(newMatches) {
    matches = newMatches;
    const grid = document.getElementById('grid');
    grid.innerHTML = '';
    matches.forEach((m, i) => {
      const url = thumbUrl(m);
      if (!url) return;
      const img = document.createElement('img');
      img.className = 'tile';
      img.src = url;
      img.title = m.filename;
      img.addEventListener('click', () => openPreview(i));
      grid.appendChild(img);
    });
  }

  function openPreview(i) {
    const m = matches[i];
    const url = previewUrl(m) || thumbUrl(m);
    if (!url) return;
    document.getElementById('preview-img').src = url;
    document.getElementById('preview').classList.add('open');
  }

  document.getElementById('preview-close').addEventListener('click', () => {
    document.getElementById('preview').classList.remove('open');
  });

  document.getElementById('btn-search').addEventListener('click', async () => {
    const status = document.getElementById('status');
    status.textContent = 'Searching\u2026';
    const args = { backend_name: backendName };
    const dateMin = document.getElementById('date-min').value.trim();
    const dateMax = document.getElementById('date-max').value.trim();
    const tags = document.getElementById('tags').value.trim();
    if (dateMin) args.date_min = dateMin;
    if (dateMax) args.date_max = dateMax;
    if (tags) args.tags = tags.split(',').map(t => t.trim()).filter(Boolean);
    try {
      const result = _standalone
        ? await httpSearch(args)
        : await callTool('search_photos', args);
      const m = result.matches || [];
      renderGrid(m);
      status.textContent = m.length + ' photos (' + (result.partitionsScanned || 0) + ' partitions scanned)';
    } catch (err) {
      status.textContent = 'Error: ' + err.message;
    }
  });
</script>
</body>
</html>"""
