"""WoofServer — FastMCP server exposing OuEstCharlie tools to Claude Desktop."""

from __future__ import annotations

import logging
import secrets
from collections import Counter
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.server.apps import AppConfig, ResourceCSP

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
        gallery_sessions: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.http_port = http_port
        self._agent = agent_client or AgentClient()
        self._gallery_sessions: dict[str, Any] = gallery_sessions if gallery_sessions is not None else {}
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
            generate_thumbnails: bool = True,
            extract_exif: bool = True,
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
                generate_thumbnails: Generate thumbnails.avif and previews.avif
                    AVIF grids. Defaults to True.
                extract_exif: Extract EXIF and create/update XMP sidecars.
                    Set to False to only rebuild manifests from existing sidecars,
                    without touching any photo or XMP file. Cannot be combined
                    with force. Defaults to True.
            """
            backend = self._require_backend(backend_name)
            tool = "index_partition_tool" if partition else "index_library_tool"
            args: dict[str, Any] = {
                "force": force,
                "generate_thumbnails": generate_thumbnails,
                "extract_exif": extract_exif,
            }
            if partition:
                args["partition"] = partition
            try:
                result = await self._agent.call_tool(
                    "whitebeard", tool, args, backend, progress_ctx=ctx
                )
            except AgentError as exc:
                _log.error("index_backend(%r) failed: %s", backend_name, exc)
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
                _log.error("search_photos(%r) failed: %s", backend_name, exc)
                return {"error": str(exc)}
            # Store matches server-side; return only a token so Claude never
            # echoes the full payload back as browse_gallery arguments.
            matches: list[Any] = result.get("matches", [])  # type: ignore[union-attr]
            token = secrets.token_urlsafe(16)
            self._gallery_sessions[token] = {
                "matches": matches,
                "backend": backend_name,
                "httpPort": self.http_port,
                "querySummary": "",
            }
            return {
                **self._search_stats(matches),
                "session_token": token,
            }

        @mcp.tool(app=AppConfig(resource_uri=_GALLERY_URI))
        async def browse_gallery(
            session_token: str,
            query_summary: str = "",
        ) -> dict[str, Any]:
            """Display photos from a search result in the gallery viewer.

            Call search_photos first, then pass its session_token here to
            open the gallery pre-loaded with the results.

            Args:
                session_token: The session_token returned by search_photos.
                query_summary: Short human-readable description of the query
                    shown in the gallery header (e.g. "Nikon photos, July 2024").
                    Leave empty to show a default title.
            """
            session = self._gallery_sessions.get(session_token)
            if session is None:
                return {"error": f"Unknown session_token {session_token!r}. Call search_photos first."}
            session["querySummary"] = query_summary
            return {
                "matches": session["matches"],
                "backend": session["backend"],
                "querySummary": query_summary,
                "httpPort": self.http_port,
            }

    # ------------------------------------------------------------------
    # Gallery resource
    # ------------------------------------------------------------------

    def _register_gallery_resource(self) -> None:
        origin = f"http://127.0.0.1:{self.http_port}"
        @self.mcp.resource(
            _GALLERY_URI,
            mime_type='text/html;profile=mcp-app',
            app=AppConfig(
                csp=ResourceCSP(
                    resource_domains=[origin],
                    connect_domains=[origin],
                )
            ),
        )
        async def gallery_resource() -> str:
            return get_gallery_html(self.http_port)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _search_stats(matches: list[Any]) -> dict[str, Any]:
        """Compute summary statistics over a list of match dicts."""
        by_partition: Counter[str] = Counter(m["partition"] for m in matches)
        dates = [m["dateTaken"] for m in matches if m.get("dateTaken")]
        ratings: Counter[int] = Counter(
            m["rating"] for m in matches if m.get("rating") is not None
        )
        return {
            "count": len(matches),
            "partitions": dict(sorted(by_partition.items())),
            "date_range": {"earliest": min(dates), "latest": max(dates)} if dates else None,
            "rating_distribution": {str(k): v for k, v in sorted(ratings.items())},
        }

    def _require_backend(self, name: str) -> BackendConfig:
        backend = self.config.get_backend(name)
        if backend is None:
            raise ValueError(
                f"Backend {name!r} not found. "
                f"Use add_backend to register it first."
            )
        return backend
