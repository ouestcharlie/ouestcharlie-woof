"""MCP server exposing OuEstCharlie tools to the host."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.apps import AppConfig, ResourceCSP
from mcp.types import ToolAnnotations

from .agent_client import AgentClient, AgentError
from .config import LibraryConfig, WoofConfig
from .gallery_session_manager import GallerySessionManager
from .http_server import get_gallery_html
from .indexing_session_manager import IndexingSessionManager

_log = logging.getLogger(__name__)

_GALLERY_URI = "ui://gallery/ouestcharlie"

# Mirrors wally.agent.FILTER_SYNTAX_DOC (kept as a separate copy rather than imported
_FILTER_SYNTAX_DOC = """\
filters: Filter expression forwarded to Wally. Three forms are accepted:

    **Single field** — one ``{"fieldName": value}`` dict::

        # All photos under the 2024/ directory tree
        {"directory": {"value": "2024", "mode": "startswith"}}

    **``{"all": [...]}``** — AND group (all must match)::

        # 4K Nikon shots in 2024
        {"all": [
            {"dateTaken": {"min": "2024", "max": "2024"}},
            {"make": "nikon"},
            {"width": {"min": 3840}}
        ]}

    **``{"any": [...]}``** — OR group (at least one must match)::

        # Photos shot on Nikon OR Canon
        {"any": [{"make": "nikon"}, {"make": "canon"}]}

    Groups can be nested::

        # 2024 photos on Nikon OR Canon
        {"all": [
            {"dateTaken": {"min": "2024", "max": "2024"}},
            {"any": [{"make": "nikon"}, {"make": "canon"}]}
        ]}
full_text_filter: Full-text search over one or more TEXT-typed
    fields. Schema::

        {"query": "Canyon", "columns": ["description"]}

    ``query`` is a single search string applied across all listed
    columns. ``columns`` must be entry_attr names of TEXT-typed
    fields (see ``list_search_fields`` → ``full_text_search.fields``).
    Results are relevance-ranked and each match includes ``_score``.
    Compatible with ``filters`` (SQL predicates applied on top of FTS)."""


def _coerce_json_param(value: Any, expected_type: type, param_name: str) -> Any:
    """Coerce a dict/list-typed MCP tool argument that arrived as a JSON string.

    Some MCP clients (observed with Claude Desktop's CoWork mode) serialize
    object/array-typed tool arguments to JSON strings instead of sending them
    as native objects/arrays. Accept both shapes here so a client-side
    serialization bug doesn't surface as an opaque downstream failure.
    """
    if value is None or isinstance(value, expected_type):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{param_name} must be a {expected_type.__name__} or JSON string"
            ) from exc
        if not isinstance(parsed, expected_type):
            raise ValueError(f"{param_name} must decode to a {expected_type.__name__}")
        return parsed
    raise ValueError(f"{param_name} must be a {expected_type.__name__} or JSON string")


class McpServer:
    """Woof MCP server.

    Exposes tools to the host and registers the gallery
    as an MCP App resource.

    Roles:
    - MCP server  → host/harness (tool registration via FastMCP)
    - MCP client  → agents (Whitebeard, Wally) via AgentClient
    """

    def __init__(
        self,
        config: WoofConfig,
        server_urls: list[str],
        agent_client: AgentClient,
        session_manager: GallerySessionManager,
        indexing_session_manager: IndexingSessionManager,
        token: str | None = None,
    ) -> None:
        """
        Args:
            server_urls: Candidate URLs (e.g. ``http://localhost:<port>``,
                ``http://127.0.0.1:<port>``) this MCP server is reachable at
                over HTTP — embedded in tool results and the gallery CSP.
            token: Bearer token, embedded in the gallery HTML / tool results
                so a frontend can authenticate against whatever serves
                *server_urls*. ``None`` is only meaningful for tests that
                construct a ``McpServer`` without ever serving it over HTTP.
        """
        self.config = config
        self._agent = agent_client
        self._sessions = session_manager
        self._indexing_sessions = indexing_session_manager
        self._library_fields: dict[str, dict[str, Any]] = {}  # library name → full Wally response
        self._token = token
        self.server_urls = server_urls
        self.server_url = server_urls[0]

        self.mcp = FastMCP("ouestcharlie-woof")
        self._register_tools()
        self._register_gallery_resource()

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
            library = LibraryConfig.create(name=name, path=path, library_type=library_type)
            self.config.add_library(library)
            _log.info("Library %r added at %s (type=%s)", name, path, library_type)
            return {**library.to_dict(), "status": "added"}

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        async def list_libraries() -> dict[str, Any]:
            """List all registered photo libraries."""
            return {"libraries": [b.to_dict() for b in self.config.libraries]}

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
            raw = await self._get_fields_raw(library)
            return {"name": library.name, **raw}

        async def _get_summary_tool(
            library_name: str = "",
            filters: dict | str | None = None,
            full_text_filter: dict | str | None = None,
        ) -> list[Any] | dict[str, Any]:
            parsed_filters: dict | None = _coerce_json_param(filters, dict, "filters")
            parsed_full_text_filter: dict | None = _coerce_json_param(
                full_text_filter, dict, "full_text_filter"
            )
            args: dict[str, Any] = {}
            if parsed_filters is not None:
                args["filters"] = parsed_filters
            if parsed_full_text_filter is not None:
                args["full_text_filter"] = parsed_full_text_filter

            if library_name:
                library = self._require_library(library_name)
                try:
                    return await self._agent.call_tool("wally", "get_summary", args, library)
                except AgentError as exc:
                    _log.error("get_summary(%r) failed: %s", library_name, exc)
                    return {"error": str(exc)}

            result = []
            for b in self.config.libraries:
                try:
                    summary = await self._agent.call_tool("wally", "get_summary", args, b)
                except AgentError as exc:
                    _log.error("get_summary(%r) failed: %s", b.name, exc)
                    summary = {"error": str(exc)}
                result.append({"name": b.name, "summary": summary})
            return result

        # Docstring assigned before registration — the decorator below reads
        # __doc__ immediately to build the tool description.
        _get_summary_tool.__doc__ = f"""\
            Return aggregate photo statistics (count, date/rating/GPS ranges, tag facets)
            for a library or a filtered scope.

            Use ``list_search_fields`` to discover available filter fields and
            their expected formats before constructing a query.

            Args:
                library_name: Name of the library to query. When omitted,
                    returns a summary for every registered library instead of one.
                {_FILTER_SYNTAX_DOC}

            Returns ``{{"error": "..."}}`` (in place of the summary) for a library
            that is unindexed or unreachable.
            """
        mcp.tool(name="get_summary", annotations=ToolAnnotations(readOnlyHint=True))(
            _get_summary_tool
        )

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
            """Index photos in a library

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
                "serverUrls": self.server_urls,
                "serverToken": self._token,
            }

        async def _search_photos_tool(
            ctx: Context,
            library_name: str,
            filters: dict | str | None = None,
            full_text_filter: dict | str | None = None,
            sort_by: str = "date_taken",
            sort_order: str = "desc",
        ) -> dict[str, Any]:
            library = self._require_library(library_name)
            parsed_filters: dict | None = _coerce_json_param(filters, dict, "filters")
            parsed_full_text_filter: dict | None = _coerce_json_param(
                full_text_filter, dict, "full_text_filter"
            )
            # Woof's MCP search always starts a 0, further pages managed by the Gallery
            page = 0
            args: dict[str, Any] = {
                "sort_by": sort_by,
                "sort_order": sort_order,
                "page": page,
            }
            if parsed_filters is not None:
                args["filters"] = parsed_filters
            if parsed_full_text_filter is not None:
                args["full_text_filter"] = parsed_full_text_filter

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
            }

        # Docstring assigned before registration — the decorator below reads
        # __doc__ immediately to build the tool description.
        _search_photos_tool.__doc__ = f"""\
            Search photos in a library

            Use ``list_search_fields`` to discover available filter fields and
            their expected formats before constructing a query.

            Args:
                library_name: Name of the library to search.
                {_FILTER_SYNTAX_DOC}
            """
        mcp.tool(name="search_photos", annotations=ToolAnnotations(readOnlyHint=True))(
            _search_photos_tool
        )

        @mcp.tool(
            annotations=ToolAnnotations(readOnlyHint=True), app=AppConfig(resource_uri=_GALLERY_URI)
        )
        async def browse_gallery(
            session_tokens: list[str] | str,
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
            tokens: list[str] = _coerce_json_param(session_tokens, list, "session_tokens")
            unknown = self._sessions.unknown_tokens(tokens)
            if unknown:
                return {
                    "error": (
                        f"Unknown session_token(s): {', '.join(repr(t) for t in unknown)}. "
                        "Call search_photos first."
                    )
                }

            merged_token, data = self._sessions.merge(tokens)
            return {
                "token": merged_token,
                "querySummary": query_summary,
                "serverUrl": self.server_url,
                "serverUrls": self.server_urls,
                "serverToken": self._token,
                "galleryUrl": f"{self.server_url}/gallery?token={merged_token}",
                "totalCount": data.totalCount,
            }

    # ------------------------------------------------------------------
    # Gallery resource
    # ------------------------------------------------------------------

    def _register_gallery_resource(self) -> None:
        @self.mcp.resource(
            _GALLERY_URI,
            mime_type="text/html;profile=mcp-app",
            app=AppConfig(
                csp=ResourceCSP(
                    resource_domains=self.server_urls,
                    connect_domains=self.server_urls,
                )
            ),
        )
        async def gallery_resource() -> str:
            return get_gallery_html(self.server_url, self.server_urls, self._token)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_fields_raw(self, library: LibraryConfig) -> dict[str, Any]:
        """Return the full Wally list_search_fields response, fetching on first call.

        The result is cached per library name for the lifetime of the server.
        Returns ``{"fields": []}`` if the agent call fails.
        """
        if library.name not in self._library_fields:
            try:
                result = await self._agent.call_tool("wally", "list_search_fields", {}, library)
                self._library_fields[library.name] = result or {}  # type: ignore[assignment]
            except AgentError as exc:
                _log.warning(
                    "list_search_fields failed for %r: %s",
                    library.name,
                    exc,
                )
                return {"fields": []}
        return self._library_fields[library.name]

    def _require_library(self, name: str) -> LibraryConfig:
        library = self.config.get_library(name)
        if library is None:
            raise ValueError(f"Library {name!r} not found. Use add_library to register it first.")
        return library
