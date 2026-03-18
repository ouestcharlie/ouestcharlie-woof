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

    Exposes six tools to Claude Desktop and registers the gallery
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
            """Get the status of all registered backends and the searchable field list.

            Returns basic information about each backend (path, existence) and
            the field definitions available for filtering in search_photos.
            Indexing status requires reading manifests — use index_backend
            to trigger indexing and search_photos to verify results.
            """
            statuses = [
                {"name": b.name, "path": b.path, "exists": Path(b.path).is_dir()}
                for b in self.config.backends
            ]
            fields: list[Any] = []
            if self.config.backends:
                try:
                    result = await self._agent.call_tool(
                        "wally", "list_search_fields_tool", {}, self.config.backends[0]
                    )
                    fields = result.get("fields", [])  # type: ignore[union-attr]
                except AgentError as exc:
                    _log.warning("get_status: could not fetch search fields: %s", exc)
            return {"backends": statuses, "fields": fields}

        @mcp.tool()
        async def get_root_manifests() -> list[Any]:
            """Return the root manifest of each registered backend.

            Manifests contain a summary of each child partition
                and an aggregated summary.
            
            Returns ``None`` for the manifest if the backend is unindexed
            or unreachable.
            """
            result = []
            for b in self.config.backends:
                try:
                    manifest = await self._agent.call_tool(
                        "wally", "get_root_manifest_tool", {}, b
                    )
                except AgentError as exc:
                    _log.warning("get_root_manifests: failed for %r: %s", b.name, exc)
                    manifest = None
                result.append({"name": b.name, "manifest": manifest})
            return result

        @mcp.tool()
        async def index_backend(
            ctx: Context,
            backend_name: str,
            partition: str = "",
            force_extract_exif: bool = False,
            generate_thumbnails: bool = True,
        ) -> dict[str, Any]:
            """Index photos in a backend using Whitebeard.

            Scans the backend for photos, writes XMP sidecars with metadata
            and content hashes, builds leaf manifests, and generates
            thumbnail/preview AVIF containers.

            Args:
                backend_name: Name of the backend to index (from list_backends).
                partition: Sub-path to index (e.g. "2024/2024-07"). Defaults
                    to "" which indexes the entire library.
                force_extract_exif: Re-extract EXIF and overwrite existing XMP
                    sidecars. Defaults to False.
                generate_thumbnails: Generate thumbnails.avif and previews.avif
                    AVIF grids. Defaults to True.
            """
            backend = self._require_backend(backend_name)
            tool = "index_partition_tool" if partition else "index_library_tool"
            args: dict[str, Any] = {
                "force_extract_exif": force_extract_exif,
                "generate_thumbnails": generate_thumbnails,
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
            filters: dict | None = None,
            root: str = "",
        ) -> dict[str, Any]:
            """Search photos in a backend using Wally.

            Traverses the manifest tree with two-level pruning for efficiency.
            Omitting ``filters`` (or passing None) returns all indexed photos.

            Use ``get_status`` to discover available filter fields and
            their expected formats before constructing a query.

            Args:
                backend_name: Name of the backend to search.
                filters: Optional dict mapping field names to filter values.
                    Call ``get_status`` to get valid fields and formats.
                    Examples::

                        # Photos taken in 2024 rated 4–5 stars
                        {"dateTaken": {"min": "2024", "max": "2024"},
                         "rating": {"min": 4, "max": 5}}

                        # Tagged "vacation" AND "portrait", shot on Nikon
                        {"tags": ["vacation", "portrait"], "make": "nikon"}

                        # 4K landscape photos
                        {"width": {"min": 3840}}

                root: Sub-path to search within the backend (default "" = entire
                    library). E.g. "2024/2024-07" to restrict to one partition.
            """
            backend = self._require_backend(backend_name)
            args: dict[str, Any] = {"root": root}
            if filters is not None:
                args["filters"] = filters

            # Fetch field definitions to drive stats computation.
            try:
                fields_result = await self._agent.call_tool(
                    "wally", "list_search_fields_tool", {}, backend
                )
                fields: list[Any] = fields_result.get("fields", [])  # type: ignore[union-attr]
            except AgentError as exc:
                _log.warning("list_search_fields_tool failed, stats will be empty: %s", exc)
                fields = []

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
                **self._search_stats(matches, fields),
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
    def _search_stats(matches: list[Any], fields: list[Any] | None = None) -> dict[str, Any]:
        """Compute summary statistics over a list of match dicts.

        ``fields`` is the descriptor list returned by Wally's
        ``list_search_fields_tool``. For each DATE_RANGE or INT_RANGE field
        a ``{name}: {min, max}`` entry is added. Field name and match dict
        key are the same by convention.

        New fields added to Wally are picked up automatically without
        any changes here.
        """
        by_partition: Counter[str] = Counter(m["partition"] for m in matches)
        stats: dict[str, Any] = {
            "count": len(matches),
            "partitions": dict(sorted(by_partition.items())),
        }
        for fdef in (fields or []):
            field_type = fdef.get("type", "")
            if field_type in ("DATE_RANGE", "INT_RANGE"):
                name = fdef.get("name", "")
                if not name:
                    continue
                values = [m[name] for m in matches if m.get(name) is not None]
                if not values:
                    stats[name] = None
                else:
                    stats[name] = {"min": min(values), "max": max(values)}
        return stats

    def _require_backend(self, name: str) -> BackendConfig:
        backend = self.config.get_backend(name)
        if backend is None:
            raise ValueError(
                f"Backend {name!r} not found. "
                f"Use add_backend to register it first."
            )
        return backend
