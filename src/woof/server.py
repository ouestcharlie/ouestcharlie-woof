"""WoofServer — FastMCP server exposing OuEstCharlie tools to Claude Desktop."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .agent_client import AgentClient, AgentError
from .config import BackendConfig, WoofConfig

_log = logging.getLogger(__name__)

_GALLERY_DIST = Path(__file__).parent / "gallery" / "dist" / "index.html"
_GALLERY_URI = "gallery://ouestcharlie"


class WoofServer:
    """Woof MCP server.

    Exposes five V1 tools to Claude Desktop and registers the gallery
    as an MCP App resource.

    Roles:
    - MCP server  → Claude Desktop (tool registration via FastMCP)
    - MCP client  → agents (Whitebeard, Wally) via AgentClient
    """

    def __init__(self, config: WoofConfig, http_port: int) -> None:
        self.config = config
        self.http_port = http_port
        self._agent = AgentClient()
        self.mcp = FastMCP("ouestcharlie-woof")
        self._register_tools()
        self._register_gallery_resource()

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        mcp = self.mcp

        @mcp.tool()
        async def add_backend(name: str, path: str) -> dict[str, Any]:
            """Register a local folder as a photo library backend.

            Args:
                name: Unique label for this backend (e.g. "iCloud Photos").
                path: Absolute path to the photo root directory.
            """
            backend = BackendConfig(name=name, type="local", path=path)
            self.config.add_backend(backend)
            _log.info("Backend %r added at %s", name, path)
            return {"name": name, "path": path, "status": "added"}

        @mcp.tool()
        async def list_backends() -> dict[str, Any]:
            """List all registered photo library backends."""
            return {
                "backends": [
                    {"name": b.name, "type": b.type, "path": b.path}
                    for b in self.config.backends
                ]
            }

        @mcp.tool()
        async def get_status() -> dict[str, Any]:
            """Get the status of all registered backends.

            Returns basic information about each backend (path, existence).
            Indexing status requires reading manifests — use index_backend
            to trigger indexing and search_photos to verify results.
            """
            statuses = []
            for b in self.config.backends:
                statuses.append({
                    "name": b.name,
                    "path": b.path,
                    "exists": Path(b.path).is_dir(),
                })
            return {"backends": statuses}

        @mcp.tool()
        async def index_backend(
            ctx: Context,
            backend_name: str,
            partition: str = "",
            force: bool = False,
        ) -> dict[str, Any]:
            """Index photos in a backend using Whitebeard.

            Scans the backend for photos, writes XMP sidecars with metadata
            and content hashes, builds leaf manifests, and generates
            thumbnail/preview AVIF containers.

            Args:
                backend_name: Name of the backend to index (from list_backends).
                partition: Sub-path to index (e.g. "2024/2024-07"). Defaults
                    to "" which indexes the entire library.
                force: Re-index photos that already have an XMP sidecar.
            """
            backend = self._require_backend(backend_name)
            tool = "index_partition_tool" if partition else "index_library_tool"
            args: dict[str, Any] = {"force": force}
            if partition:
                args["partition"] = partition
            try:
                result = await self._agent.call_tool(
                    "whitebeard", tool, args, backend, progress_ctx=ctx
                )
            except AgentError as exc:
                return {"error": str(exc)}
            return result  # type: ignore[return-value]

        @mcp.tool()
        async def search_photos(
            ctx: Context,
            backend_name: str,
            date_min: str | None = None,
            date_max: str | None = None,
            tags: list[str] | None = None,
            rating_min: int | None = None,
            rating_max: int | None = None,
            make: str | None = None,
            model: str | None = None,
        ) -> dict[str, Any]:
            """Search photos in a backend using Wally.

            Traverses the manifest tree with two-level pruning for efficiency.
            All parameters are optional — omitting all returns all indexed photos.

            Args:
                backend_name: Name of the backend to search.
                date_min: Inclusive lower bound on date taken. Partial dates
                    accepted: "2024" expands to 2024-01-01, "2024-07" to
                    2024-07-01.
                date_max: Inclusive upper bound. "2024-07" expands to
                    2024-07-31T23:59:59.
                tags: All tags must be present (AND semantics). Matches
                    dc:subject values.
                rating_min: Minimum xmp:Rating (0=unrated, 1–5=stars,
                    -1=rejected).
                rating_max: Maximum xmp:Rating.
                make: Case-insensitive substring match on camera make.
                model: Case-insensitive substring match on camera model.
            """
            backend = self._require_backend(backend_name)
            args: dict[str, Any] = {}
            if date_min is not None:
                args["date_min"] = date_min
            if date_max is not None:
                args["date_max"] = date_max
            if tags is not None:
                args["tags"] = tags
            if rating_min is not None:
                args["rating_min"] = rating_min
            if rating_max is not None:
                args["rating_max"] = rating_max
            if make is not None:
                args["make"] = make
            if model is not None:
                args["model"] = model

            try:
                result = await self._agent.call_tool(
                    "wally", "search_photos_tool", args, backend, progress_ctx=ctx
                )
            except AgentError as exc:
                return {"error": str(exc)}
            return result  # type: ignore[return-value]

        @mcp.tool()
        async def browse_gallery(backend_name: str) -> dict[str, Any]:
            """Open the photo gallery for a backend.

            Returns an MCP App resource reference that Claude Desktop renders
            as an interactive iframe.  The gallery communicates with Woof via
            the MCP App postMessage protocol.

            Args:
                backend_name: Name of the backend to browse.
            """
            self._require_backend(backend_name)
            return {
                "_meta": {"ui": {"resourceUri": _GALLERY_URI}},
                "backend": backend_name,
                "httpPort": self.http_port,
            }

    # ------------------------------------------------------------------
    # Gallery resource
    # ------------------------------------------------------------------

    def _register_gallery_resource(self) -> None:
        @self.mcp.resource(_GALLERY_URI)
        async def gallery_resource() -> str:
            if not _GALLERY_DIST.exists():
                return _gallery_placeholder()
            return _GALLERY_DIST.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_backend(self, name: str) -> BackendConfig:
        backend = self.config.get_backend(name)
        if backend is None:
            raise ValueError(
                f"Backend {name!r} not found. "
                f"Use add_backend to register it first."
            )
        return backend


def _gallery_placeholder() -> str:
    """Minimal HTML served when no built gallery bundle is present."""
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
  <div id="status">Ready</div>
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

  // MCP App bridge
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
    return `http://127.0.0.1:${httpPort}/thumbnails/${backendName}/${partition}/thumbnails.avif`;
  }

  function previewUrl(match) {
    if (!httpPort || !match.previewsPath) return null;
    const partition = encodeURIComponent(match.partition).replace(/%2F/g, '/');
    return `http://127.0.0.1:${httpPort}/previews/${backendName}/${partition}/previews.avif`;
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
    status.textContent = 'Searching…';
    const args = { backend_name: backendName };
    const dateMin = document.getElementById('date-min').value.trim();
    const dateMax = document.getElementById('date-max').value.trim();
    const tags = document.getElementById('tags').value.trim();
    if (dateMin) args.date_min = dateMin;
    if (dateMax) args.date_max = dateMax;
    if (tags) args.tags = tags.split(',').map(t => t.trim()).filter(Boolean);
    try {
      const result = await callTool('search_photos', args);
      const m = result.matches || [];
      renderGrid(m);
      status.textContent = `${m.length} photos (${result.partitionsScanned || 0} partitions scanned)`;
    } catch (err) {
      status.textContent = 'Error: ' + err.message;
    }
  });
</script>
</body>
</html>"""
