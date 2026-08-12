"""MCP server exposing OuEstCharlie tools to the host."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.apps import AppConfig, ResourceCSP
from mcp.types import ToolAnnotations
from pydantic import BeforeValidator

from .agent_client import AgentClient, AgentError
from .config import LibraryConfig, WoofConfig
from .gallery_session_manager import GallerySessionManager
from .http_server import get_gallery_html
from .indexing_session_manager import IndexingSessionManager

_log = logging.getLogger(__name__)

_GALLERY_URI = "ui://gallery/ouestcharlie"

# Mirrors wally.agent.FILTER_SYNTAX_DOC (kept as a separate copy rather than imported
# Shared filter-syntax documentation, embedded in both search_photos and
# get_summary's docstrings. Kept textually identical to Wally's copy of the same
# constant so the two MCP layers present the same filter/full-text vocabulary.
# Sort documentation lives in _SORT_SYNTAX_DOC (search_photos only) — get_summary
# has no sort argument.
_FILTER_SYNTAX_DOC = """\
filters: Filter expression. Three forms are accepted:

    **Single field** — one ``{"fieldName": value}`` dict::

        # media captured during an activity — full timestamps on both bounds
        {"dateTaken": {"min": "2026-07-15T07:46:41", "max": "2026-07-15T09:37:05"}}

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

    Tags are cumulative (AND relationship):
        # everything tagged Famille AND Vacances
        {"tags": ["Famille", "Vacances"]}
full_text_filter: Full-text search over one or more TEXT-typed
    fields. Schema::

        {"query": "Canyon", "columns": ["description"]}

    ``query`` is a single search string applied across all listed
    columns. ``columns`` must be entry_attr names of TEXT-typed
    fields (see ``list_search_fields`` → ``full_text_search.fields``).
    Results are relevance-ranked and each match includes ``_score``.
    Compatible with ``filters`` (SQL predicates applied on top of FTS)."""

# Sort documentation for search_photos only. Kept textually identical to Wally's
# copy. Not part of _FILTER_SYNTAX_DOC because get_summary shares that block and
# accepts no sort argument.
_SORT_SYNTAX_DOC = """\
sort_by: Field name to sort results by — one of the ``list_search_fields``
    names marked ``sortable`` (e.g. ``dateTaken``, ``rating``). Defaults to
    ``dateTaken``. Unknown or non-sortable names are rejected.
sort_order: ``asc`` or ``desc`` (default ``desc``)."""


def _json_coercer(expected_type: type, param_name: str) -> Callable[[Any], Any]:
    """Build a pydantic ``BeforeValidator`` that tolerates JSON-stringified arguments.

    Some MCP clients (observed with Claude Desktop's CoWork mode) serialize
    object/array-typed tool arguments to JSON strings instead of sending them
    as native objects/arrays. Running as a ``BeforeValidator`` (i.e. before
    pydantic's own type check) lets the argument keep a clean ``array``/``object``
    JSON schema — so well-behaved clients render the correct input widget — while
    a stringified value is still parsed rather than rejected.
    """

    def _coerce(value: Any) -> Any:
        if not isinstance(value, str):
            # Native value or None: let pydantic validate against expected_type.
            return value
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{param_name} must be a {expected_type.__name__} or JSON string"
            ) from exc
        return parsed

    return _coerce


# Reusable argument annotations: clean array/object schema + JSON-string tolerance.
# Callers supply a matching literal default (``= []`` / ``= {}``); a mutable default is
# safe here because the tool bodies only read these arguments, never mutate them.
JsonStrList = Annotated[list[str], BeforeValidator(_json_coercer(list, "argument"))]
JsonDict = Annotated[dict, BeforeValidator(_json_coercer(dict, "argument"))]


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
            token: The master bearer token that guards the HTTP server. Not
                embedded in tool results or the gallery HTML — the frontend
                authenticates with per-session tokens instead (the merged
                ``token`` from ``browse_gallery``, the ``session_id`` from
                ``index_library``). ``None`` is only meaningful for tests that
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
        async def register_library(
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

        @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
        async def unregister_library(
            library_name: str,
            purge_metadata: bool = False,
        ) -> dict[str, Any]:
            """Unregister a photo library.

            By default this only removes the library from Woof's configuration —
            nothing on disk is touched, and the library can be re-registered
            later at the same path. Photos and XMP sidecars are always left
            intact.

            Args:
                library_name: Name of the library to unregister (from
                    list_libraries).
                purge_metadata: When True, also delete the library's
                    ``.ouestcharlie/`` metadata directory (manifests, index,
                    thumbnails, previews) at the library root. XMP sidecars are
                    never deleted. Defaults to False.

            Returns:
                ``name`` — the library that was unregistered.
                ``status`` — ``"removed"``.
                ``metadataPurged`` — True if ``.ouestcharlie/`` was deleted.
            """
            library = self._require_library(library_name)
            if purge_metadata:
                # Delegate the destructive delete to Whitebeard (the write
                # agent that owns .ouestcharlie/); only forget on success so a
                # failed purge leaves the library registered for a retry.
                await self._agent.call_tool("whitebeard", "purge_metadata", {}, library)
            self.config.remove_library(library_name)
            _log.info("Library %r removed (purge_metadata=%s)", library_name, purge_metadata)
            return {"name": library_name, "status": "removed", "metadataPurged": purge_metadata}

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        async def list_libraries() -> dict[str, Any]:
            """List all registered photo libraries."""
            return {"libraries": [b.to_dict() for b in self.config.libraries]}

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        async def list_search_fields(library_name: str = "") -> dict:
            """List all searchable photo fields with their types and filter formats.

            Returns a ``fields`` list of descriptors. Use the field names and formats
            described here when constructing the ``filters`` argument for
            ``search_photos`` and ``get_summary``.
            The same ``name`` values are used as ``sort_by`` keys —
            only fields marked ``sortable`` may be passed to ``sort_by``.

            Args:
                library_name: Name of the library to query. Defaults to the
                    first registered library when omitted.

            Returns:
                ``name`` — the library these fields belong to.
                ``fields`` — list of field descriptors, each with:
                    ``name`` — field name to use as key in ``filters`` and as ``sort_by``.
                    ``type`` — semantic type (DATE_RANGE, INT_RANGE, STRING_COLLECTION,
                        STRING_MATCH, GPS_BOX, DESCRIPTIVE).
                    ``filterFormat`` — description of the expected value format.
                    ``sortable`` — True if this field can be used as a ``sort_by`` key.
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
            filters: JsonDict = {},  # noqa: B006 — pydantic Field(default_factory) supplies the real default
            full_text_filter: JsonDict = {},  # noqa: B006
        ) -> list[Any] | dict[str, Any]:
            args: dict[str, Any] = {}
            if filters:
                args["filters"] = filters
            if full_text_filter:
                args["full_text_filter"] = full_text_filter

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
        _get_summary_tool.__doc__ = f"""Compute aggregate statistics for photos matching a filter.

            Returns count, per-field ranges (date, rating, width/height,
            duration, GPS bounding box), categorical facets (media type, video
            codec, tags), and boolean counts (has-audio). An empty ``filters``
            summarizes the whole library. Scope it to a query using the filter,
            optionally combined with ``full_text_filter`` — same semantics as
            ``search_photos``.

            Use ``list_search_fields`` to discover all available fields and
            their expected filter formats.

            Args:
                library_name: Name of the library to query. When omitted,
                    returns a summary for every registered library instead of one.
                {_FILTER_SYNTAX_DOC}

            Returns:
                For a single library (``library_name`` given), the summary dict:
                ``mediaCount`` — number of matching items.
                Per-field range stats (``dateTaken``, ``rating``, ``width``,
                ``height``, ``durationSeconds``, ``gps``), each present only if at
                least one matching item has a value for that field. Ranges carry
                ``{{"type": "date_range"|"int_range"|"float_range", "min", "max"}}``.
                Categorical facets (``mediaType``, ``videoCodec``, ``tags``) —
                ``{{"type": "string_facets"|"tag_facets", "counts": {{value: count}}}}``.
                Boolean counts (``hasAudio``) —
                ``{{"type": "bool_counts", "true": N, "false": M}}``.
                Each stat is present only when the matching set has values for it —
                e.g. a photo-only result carries no ``videoCodec``/``durationSeconds``.

                When ``library_name`` is omitted, a list of
                ``{{"name": <library>, "summary": <summary dict above>}}`` — one per
                registered library. A library that is unindexed or unreachable yields
                ``{{"error": "..."}}`` in place of its summary.

            Note:
                Results reflect the *index*, not the files on disk. Editing an XMP
                sidecar does not reflect in the index until next re-index.
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
            partition_scope: JsonStrList = [],  # noqa: B006 — read-only, tolerant of stringified arrays
            force_full_index: bool = False,
            force_extract_exif: bool = False,
            generate_thumbnails: bool = True,
        ) -> dict[str, Any]:
            """Index photos in a library

            Launches indexing as a background task and returns immediately.
            Progress is shown in the gallery app; the summary is sent back
            to the model context when indexing completes.

            By default runs in incremental mode: only new photos are indexed,
            deleted photos are removed from the index. Sidecar edits on
            unchanged media are not picked up.
            Use ``force_full_index=True`` to re-process all photos.

            Scans the library for photos, writes XMP sidecars with metadata
            and content hashes, builds leaf manifests, and generates
            thumbnail AVIF containers.

            Args:
                library_name: Name of the library to index (from list_libraries).
                partition_scope: Partitions to index (e.g. ["2024/2024-07"]).
                    Partitions are relative to the library root.
                    Each entry indexes only its direct-child photos, not
                    descendant subfolders.
                    Defaults to None/empty, which indexes the entire library
                    (walking every subfolder).
                force_full_index: Re-process all photos even if already indexed.
                    Defaults to False (incremental).
                force_extract_exif: Re-read EXIF from every media file and regenerate its
                    XMP sidecar.
                    DESTRUCTIVE: regenerated sidecars lose any enrichment like
                    dc:description and dc:subject written by other tools.
                    Not needed for a normal refresh — use force_full_index.
                    Defaults to False.
                generate_thumbnails: Generate thumbnails.avif AVIF grids.
                    Defaults to True.
            """
            library = self._require_library(library_name)
            base_args: dict[str, Any] = {
                "force_extract_exif": force_extract_exif,
                "generate_thumbnails": generate_thumbnails,
                "force_full_index": force_full_index,
            }

            session_id = self._indexing_sessions.start(library_name, partition_scope)

            def _on_progress(progress: float, total: float, message: str) -> None:
                self._indexing_sessions.update(session_id, progress, total, message)

            def _on_complete(result: Any) -> None:
                self._indexing_sessions.complete(session_id, result)

            def _on_error(exc: Exception) -> None:
                if isinstance(exc, asyncio.CancelledError):
                    self._indexing_sessions.cancelled(session_id)
                else:
                    self._indexing_sessions.fail(session_id, str(exc))

            if partition_scope:
                tool_name = "index_partition_scope"
                args = {**base_args, "partition_scope": partition_scope}
            else:
                tool_name = "index_library"
                args = base_args

            task = self._agent.call_tool_background(
                "whitebeard",
                tool_name,
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
                "partition_scope": partition_scope,
                "serverUrl": self.server_url,
                "serverUrls": self.server_urls,
            }

        async def _search_photos_tool(
            ctx: Context,
            library_name: str,
            filters: JsonDict = {},  # noqa: B006 — read-only, tolerant of stringified objects
            full_text_filter: JsonDict = {},  # noqa: B006
            sort_by: str = "dateTaken",
            sort_order: str = "desc",
        ) -> dict[str, Any]:
            library = self._require_library(library_name)
            # The MCP client never paginates — it only hands the session_id to
            # browse_gallery. Page navigation happens entirely in the gallery's HTTP
            # backend (GallerySession.fetch_page), which re-issues the query per page.
            # So query_args carry no "page" key; fetch_page supplies it, and this
            # initial call requests page 0 explicitly.
            args: dict[str, Any] = {
                "sort_by": sort_by,
                "sort_order": sort_order,
            }
            if filters:
                args["filters"] = filters
            if full_text_filter:
                args["full_text_filter"] = full_text_filter

            try:
                result = await self._agent.call_tool(
                    "wally", "search_photos", {**args, "page": 0}, library, progress_ctx=ctx
                )
            except AgentError as exc:
                _log.error("search_photos(%r) failed: %s", library_name, exc)
                return {"error": str(exc)}
            # Store matches server-side; return only a session id so Claude never
            # echoes the full payload back as browse_gallery arguments.
            matches: list[Any] = result.get("matches", [])  # type: ignore[union-attr]
            session_id = self._sessions.create(
                library=library,
                agent=self._agent,
                query_args=args,
                total_count=result.get("totalCount"),
                page=0,
                page_size=result.get("pageSize", 500),
                matches=matches,
            )
            return {
                "session_id": session_id,
                "totalCount": result.get("totalCount", len(matches)),
                "errors": result.get("errors", 0),
                "errorDetails": result.get("errorDetails", []),
            }

        # Docstring assigned before registration — the decorator below reads
        # __doc__ immediately to build the tool description.
        _search_photos_tool.__doc__ = f"""\
            Search photos index in a library matching structured predicates.

            Use ``list_search_fields`` to discover available filter fields and
            their expected formats before constructing a query.

            Returns a session id only — nothing is displayed.
            Pass the session id to ``browse_gallery`` to show results.

            Args:
                library_name: Name of the library to search.
                {_FILTER_SYNTAX_DOC}
                {_SORT_SYNTAX_DOC}

            Returns:
                ``session_id`` — opaque handle to the stored results; pass it
                to ``browse_gallery``. The matches themselves are held server-side and
                are not returned here. Pagination is handled by the gallery, not the
                caller.
                ``totalCount`` — total matches for the query.
                ``errors`` — count of read failures.
                ``errorDetails`` — per-failure error messages.
            """
        mcp.tool(name="search_photos", annotations=ToolAnnotations(readOnlyHint=True))(
            _search_photos_tool
        )

        @mcp.tool(
            annotations=ToolAnnotations(readOnlyHint=True), app=AppConfig(resource_uri=_GALLERY_URI)
        )
        async def browse_gallery(
            session_ids: JsonStrList,
            query_summary: str = "",
        ) -> dict[str, Any]:
            """Display photos from one or more search results in the gallery viewer.

            Call search_photos one or more times, then pass all returned
            session_ids here.  Matches are merged and deduplicated by
            content hash so the same photo never appears twice even when it
            is returned by several queries.

            Args:
                session_ids: List of session_id values returned by
                    search_photos.  Pass a single-element list when showing
                    one query's results.
                query_summary: Short human-readable description shown in the
                    gallery header (e.g. "Nikon photos, July 2024").
                    Leave empty to show a default title.
            """
            unknown = self._sessions.unknown_session_ids(session_ids)
            if unknown:
                return {
                    "error": (
                        f"Unknown session_id(s): {', '.join(repr(t) for t in unknown)}. "
                        "Call search_photos first."
                    )
                }

            merged_session_id, data = self._sessions.merge(session_ids)
            # `session_id` is the frontend's credential for its /gallery/{session_id}/…
            # routes; the full URL is derivable from serverUrl + session_id.
            return {
                "session_id": merged_session_id,
                "querySummary": query_summary,
                "serverUrl": self.server_url,
                "serverUrls": self.server_urls,
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
            # No token embedded: this static MCP resource is shared across sessions,
            # so the frontend receives its per-session token from the tool result
            # (browse_gallery's `token` / index_library's `session_id`) instead.
            return get_gallery_html(self.server_url, self.server_urls, None)

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
            raise ValueError(f"""\
                Library {name!r} not found. Use list_libraries to get existing libraries
                or register_library to register a new one.""")
        return library
