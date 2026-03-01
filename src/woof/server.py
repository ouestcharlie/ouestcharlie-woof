"""WoofServer — FastMCP server exposing OuEstCharlie tools to Claude Desktop."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .agent_client import AgentClient, AgentError
from .config import BackendConfig, WoofConfig
from .http_server import get_gallery_html

_log = logging.getLogger(__name__)

_GALLERY_URI = "ui://gallery/ouestcharlie"


class WoofServer:
    """Woof MCP server.

    Exposes five V1 tools to Claude Desktop and registers the gallery
    as an MCP App resource.

    Roles:
    - MCP server  → Claude Desktop (tool registration via FastMCP)
    - MCP client  → agents (Whitebeard, Wally) via AgentClient
    """

    def __init__(
        self,
        config: WoofConfig,
        http_port: int,
        agent_client: AgentClient | None = None,
    ) -> None:
        self.config = config
        self.http_port = http_port
        self._agent = agent_client or AgentClient()
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

            Returns a URL to open in a browser and, when running inside
            Claude Desktop with MCP App support, an iframe resource reference.

            Args:
                backend_name: Name of the backend to browse.
            """
            self._require_backend(backend_name)
            url = f"http://127.0.0.1:{self.http_port}/gallery?backend={backend_name}"
            return {
                "_meta": {"ui": {"resourceUri": _GALLERY_URI}},
                "url": url,
                "backend": backend_name,
                "httpPort": self.http_port,
            }

    # ------------------------------------------------------------------
    # Gallery resource
    # ------------------------------------------------------------------

    def _register_gallery_resource(self) -> None:
        @self.mcp.resource(_GALLERY_URI, mime_type="text/html")
        async def gallery_resource() -> str:
            return get_gallery_html()

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
