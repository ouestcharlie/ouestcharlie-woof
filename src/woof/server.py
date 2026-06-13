"""WoofServer — FastMCP server exposing OuEstCharlie tools to Claude Desktop."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.apps import AppConfig, ResourceCSP
from mcp.types import ToolAnnotations

from .agent_client import AgentClient, AgentError
from .config import LibraryConfig, WoofConfig
from .gallery_session_manager import GallerySessionManager
from .http_server import get_gallery_html, serve_in_loop
from .indexing_session_manager import IndexingSessionManager

_log = logging.getLogger(__name__)

_GALLERY_URI = "ui://gallery/ouestcharlie"


class WoofServer:
    """Woof MCP server.

    Exposes tools to Claude Desktop and registers the gallery
    as an MCP App resource.

    Roles:
    - MCP server  → Claude Desktop (tool registration via FastMCP)
    - MCP client  → agents (Whitebeard, Wally) via AgentClient
    """

    def __init__(
        self,
        config: WoofConfig,
        agent_client: AgentClient | None = None,
        session_manager: GallerySessionManager | None = None,
    ) -> None:
        self.config = config
        self._agent = agent_client or AgentClient()
        self._sessions = session_manager if session_manager is not None else GallerySessionManager()
        self._indexing_sessions = IndexingSessionManager()
        self._library_fields: dict[str, list[Any]] = {}  # library name → field defs, loaded lazily

        # Bind port before MCP starts so server_url is known synchronously.
        _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _sock.bind(("127.0.0.1", 0))
        self._http_sock = _sock
        self.server_url = f"http://localhost:{_sock.getsockname()[1]}"

        agent = self._agent

        @asynccontextmanager
        async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
            # HTTP server shares this event loop — no cross-thread bridging needed.
            # Any synchronous work in request handlers must use run_in_executor.

            http_task = asyncio.create_task(
                serve_in_loop(
                    self._http_sock,
                    self._sessions,
                    self._wally_connection,
                    self.server_url,
                    indexing_session_manager=self._indexing_sessions,
                )
            )
            try:
                yield
            finally:
                http_task.cancel()
                await asyncio.gather(http_task, return_exceptions=True)
                await agent.shutdown()

        self.mcp = FastMCP("ouestcharlie-woof", lifespan=_lifespan)
        self._register_tools()
        self._register_gallery_resource()

    def _wally_connection(self, library_name: str) -> tuple[int | None, str | None]:
        return self._agent.get_wally_connection(library_name)

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        mcp = self.mcp

        @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
        async def add_library(
            name: str,
            path: str,
            library_type: str = "filesystem",
        ) -> dict[str, Any]:
            """Register a photo library.

            Args:
                name: Unique label for this library (e.g. "kDrive Photos").
                path: Absolute path to the photo root directory.
                library_type: Storage type. Use ``"filesystem"`` for a normal
                    local folder (default) or ``"cloud_mount"`` for a
                    FUSE/Windows-CF-API cloud-sync folder (kDrive, OneDrive,
                    Google Drive, Dropbox).
            """
            library = LibraryConfig(name=name, type=library_type, path=path)
            self.config.add_library(library)
            _log.info("Library %r added at %s (type=%s)", name, path, library_type)
            return {"name": name, "path": path, "type": library_type, "status": "added"}

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        async def list_libraries() -> dict[str, Any]:
            """List all registered photo libraries."""
            return {
                "libraries": [
                    {"name": b.name, "type": b.type, "path": b.path} for b in self.config.libraries
                ]
            }

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        async def list_search_fields(library_name: str = "") -> dict:
            """Get the searchable field definitions for a library.

            Returns the field definitions available for filtering in search_photos.

            Args:
                library_name: Name of the library to query. Defaults to the
                    first registered library when omitted.
            """
            if not self.config.libraries:
                return {}
            if library_name:
                library = self._require_library(library_name)
            else:
                library = self.config.libraries[0]
            return {"name": library.name, "fields": await self._get_fields(library)}

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        async def get_partition_summaries() -> list[Any]:
            """Return the root summary of each registered library.

            The summary contains a flat list of all indexed partitions with
            photo counts and statistics (date ranges, GPS bounding boxes, etc.).

            Returns ``None`` for the summary if the library is unindexed or unreachable.
            """
            result = []
            for b in self.config.libraries:
                try:
                    summary = await self._agent.call_tool("wally", "get_partition_summaries", {}, b)
                except AgentError as exc:
                    _log.warning("get_partition_summaries: failed for %r: %s", b.name, exc)
                    summary = None
                result.append({"name": b.name, "summary": summary})
            return result

        @mcp.tool(
            annotations=ToolAnnotations(destructiveHint=True),
            app=AppConfig(resource_uri=_GALLERY_URI),
        )
        async def index_library(
            library_name: str,
            partition: str = "",
            force_extract_exif: bool = False,
            generate_thumbnails: bool = True,
            force_full_index: bool = False,
        ) -> dict[str, Any]:
            """Index photos in a library using Whitebeard.

            Launches indexing as a background task and returns immediately.
            Progress is shown in the gallery app; the summary is sent back
            to the model context when indexing completes.

            By default runs in incremental mode: only new photos are indexed,
            deleted photos are removed from the manifest.  Use
            ``force_full_index=True`` to re-process all photos.

            Scans the library for photos, writes XMP sidecars with metadata
            and content hashes, builds leaf manifests, and generates
            thumbnail AVIF containers.

            Args:
                library_name: Name of the library to index (from list_libraries).
                partition: Sub-path to index (e.g. "2024/2024-07"). Defaults
                    to "" which indexes the entire library.
                force_extract_exif: Re-extract EXIF and overwrite existing XMP
                    sidecars. Defaults to False.
                generate_thumbnails: Generate thumbnails.avif AVIF grids.
                    Defaults to True.
                force_full_index: Re-process all photos even if already indexed.
                    Defaults to False (incremental).
            """
            library = self._require_library(library_name)
            tool = "index_partition" if partition else "index_library"
            args: dict[str, Any] = {
                "force_extract_exif": force_extract_exif,
                "generate_thumbnails": generate_thumbnails,
                "force_full_index": force_full_index,
            }
            if partition:
                args["partition"] = partition

            session_id = self._indexing_sessions.start(library_name, partition)

            def _on_progress(progress: float, total: float, message: str) -> None:
                self._indexing_sessions.update(session_id, progress, total, message)

            def _on_complete(result: Any) -> None:
                self._indexing_sessions.complete(session_id, result)

            def _on_error(exc: Exception) -> None:
                if isinstance(exc, asyncio.CancelledError):
                    self._indexing_sessions.cancelled(session_id)
                else:
                    self._indexing_sessions.fail(session_id, str(exc))

            task = self._agent.call_tool_background(
                "whitebeard",
                tool,
                args,
                library,
                on_progress=_on_progress,
                on_complete=_on_complete,
                on_error=_on_error,
            )
            self._indexing_sessions.register_task(session_id, task)

            return {
                "type": "indexing",
                "session_id": session_id,
                "library_name": library_name,
                "partition": partition,
                "serverUrl": self.server_url,
            }

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        async def search_photos(
            ctx: Context,
            library_name: str,
            filters: dict | None = None,
            root: str = "",
            sort_by: str = "date_taken",
            sort_order: str = "desc",
        ) -> dict[str, Any]:
            """Search photos in a library using Wally.

            Traverses the manifest tree with two-level pruning for efficiency.
            Omitting ``filters`` (or passing None) returns all indexed photos.

            Use ``list_search_fields`` to discover available filter fields and
            their expected formats before constructing a query.

            Args:
                library_name: Name of the library to search.
                filters: Optional dict mapping field names to filter values.
                    Call ``list_search_fields`` to get valid fields and formats.
                    Examples::

                        # Photos taken in 2024 rated 4–5 stars
                        {"dateTaken": {"min": "2024", "max": "2024"},
                         "rating": {"min": 4, "max": 5}}

                        # Tagged "vacation" AND "portrait", shot on Nikon
                        {"tags": ["vacation", "portrait"], "make": "nikon"}

                        # 4K landscape photos
                        {"width": {"min": 3840}}

                root: Sub-path to search within the library (default "" = entire
                    library). E.g. "2024/2024-07" to restrict to one partition.
            """
            library = self._require_library(library_name)
            # Woof's MCP search always starts a 0, further pages managed by the Gallery
            page = 0
            args: dict[str, Any] = {
                "root": root,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "page": page,
            }
            if filters is not None:
                args["filters"] = filters

            fields = await self._get_fields(library)

            try:
                result = await self._agent.call_tool(
                    "wally", "search_photos", args, library, progress_ctx=ctx
                )
            except AgentError as exc:
                _log.error("search_photos(%r) failed: %s", library_name, exc)
                return {"error": str(exc)}
            # Store matches server-side; return only a token so Claude never
            # echoes the full payload back as browse_gallery arguments.
            matches: list[Any] = result.get("matches", [])  # type: ignore[union-attr]
            page_size: int = result.get("pageSize", 500)
            token = self._sessions.create(
                library=library,
                agent=self._agent,
                query_args=args,
                total_count=result.get("totalCount"),
                page=page,
                page_size=page_size,
                matches=matches,
            )
            return {
                "session_token": token,
                "totalCount": result.get("totalCount", len(matches)),
                "page": result.get("page", 0),
                "pageSize": page_size,
                "hasMore": result.get("hasMore", False),
                "errors": result.get("errors", 0),
                "errorDetails": result.get("errorDetails", []),
                "pageStats": self._search_stats(matches, fields),
            }

        @mcp.tool(
            annotations=ToolAnnotations(readOnlyHint=True), app=AppConfig(resource_uri=_GALLERY_URI)
        )
        async def browse_gallery(
            session_tokens: list[str],
            query_summary: str = "",
        ) -> dict[str, Any]:
            """Display photos from one or more search results in the gallery viewer.

            Call search_photos one or more times, then pass all returned
            session_tokens here.  Matches are merged and deduplicated by
            content hash so the same photo never appears twice even when it
            is returned by several queries.

            Args:
                session_tokens: List of session_token values returned by
                    search_photos.  Pass a single-element list when showing
                    one query's results.
                query_summary: Short human-readable description shown in the
                    gallery header (e.g. "Nikon photos, July 2024").
                    Leave empty to show a default title.
            """
            unknown = self._sessions.unknown_tokens(session_tokens)
            if unknown:
                return {
                    "error": (
                        f"Unknown session_token(s): {', '.join(repr(t) for t in unknown)}. "
                        "Call search_photos first."
                    )
                }

            merged_token, data = self._sessions.merge(session_tokens)
            return {
                "token": merged_token,
                "querySummary": query_summary,
                "serverUrl": self.server_url,
                "galleryUrl": f"{self.server_url}/gallery?token={merged_token}",
                "totalCount": data.totalCount,
            }

    # ------------------------------------------------------------------
    # Gallery resource
    # ------------------------------------------------------------------

    def _register_gallery_resource(self) -> None:
        origin = self.server_url

        @self.mcp.resource(
            _GALLERY_URI,
            mime_type="text/html;profile=mcp-app",
            app=AppConfig(
                csp=ResourceCSP(
                    resource_domains=[origin],
                    connect_domains=[origin],
                )
            ),
        )
        async def gallery_resource() -> str:
            return get_gallery_html(self.server_url)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _search_stats(matches: list[Any], fields: list[Any] | None = None) -> dict[str, Any]:
        """Compute summary statistics over a list of match dicts.

        ``fields`` is the descriptor list returned by Wally's
        ``list_search_fields``. For each DATE_RANGE or INT_RANGE field
        a ``{name}: {min, max}`` entry is added. Field name and match dict
        key are the same by convention.

        New fields added to Wally are picked up automatically without
        any changes here.
        """
        by_partition: Counter[str] = Counter(m["partition"] for m in matches)
        stats: dict[str, Any] = {
            "partitions": dict(sorted(by_partition.items())),
        }
        for fdef in fields or []:
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

    async def _get_fields(self, library: LibraryConfig) -> list[Any]:
        """Return field definitions for a library, fetching from Wally on first call.

        The result is cached per library name for the lifetime of the server.
        Returns an empty list if the agent call fails.
        """
        if library.name not in self._library_fields:
            try:
                result = await self._agent.call_tool("wally", "list_search_fields", {}, library)
                self._library_fields[library.name] = result.get("fields", [])  # type: ignore[union-attr]
            except AgentError as exc:
                _log.warning(
                    "list_search_fields failed for %r, stats will be empty: %s",
                    library.name,
                    exc,
                )
                return []
        return self._library_fields[library.name]

    def _require_library(self, name: str) -> LibraryConfig:
        library = self.config.get_library(name)
        if library is None:
            raise ValueError(f"Library {name!r} not found. Use add_library to register it first.")
        return library
