"""WoofServer — FastMCP server exposing OuEstCharlie tools to Claude Desktop."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.apps import AppConfig, ResourceCSP
from mcp.types import ResourceLink, TextContent

from .agent_client import AgentClient, AgentError
from .config import BackendConfig, WoofConfig
from .gallery_session_manager import GallerySessionManager
from .http_server import get_gallery_html

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
        http_port: int,
        agent_client: AgentClient | None = None,
        session_manager: GallerySessionManager | None = None,
    ) -> None:
        self.config = config
        self.http_port = http_port
        self._agent = agent_client or AgentClient()
        self._sessions = session_manager if session_manager is not None else GallerySessionManager()
        self._backend_fields: dict[str, list[Any]] = {}  # backend name → field defs, loaded lazily

        agent = self._agent

        @asynccontextmanager
        async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
            try:
                yield
            finally:
                await agent.shutdown()

        self.mcp = FastMCP("ouestcharlie-woof", lifespan=_lifespan)
        self._register_tools()
        self._register_gallery_resource()
        self._register_photo_resource()

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
                    {"name": b.name, "type": b.type, "path": b.path} for b in self.config.backends
                ]
            }

        @mcp.tool()
        async def list_search_fields(backend_name: str = "") -> dict:
            """Get the searchable field definitions for a backend.

            Returns the field definitions available for filtering in search_photos.

            Args:
                backend_name: Name of the backend to query. Defaults to the
                    first registered backend when omitted.
            """
            if not self.config.backends:
                return {}
            if backend_name:
                backend = self._require_backend(backend_name)
            else:
                backend = self.config.backends[0]
            return {"name": backend.name, "fields": await self._get_fields(backend)}

        @mcp.tool()
        async def get_partition_summaries() -> list[Any]:
            """Return the root summary of each registered backend.

            The summary contains a flat list of all indexed partitions with
            photo counts and statistics (date ranges, GPS bounding boxes, etc.).

            Returns ``None`` for the summary if the backend is unindexed or unreachable.
            """
            result = []
            for b in self.config.backends:
                try:
                    summary = await self._agent.call_tool("wally", "get_partition_summaries", {}, b)
                except AgentError as exc:
                    _log.warning("get_partition_summaries: failed for %r: %s", b.name, exc)
                    summary = None
                result.append({"name": b.name, "summary": summary})
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
            tool = "index_partition" if partition else "index_library"
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

            Use ``list_search_fields`` to discover available filter fields and
            their expected formats before constructing a query.

            Args:
                backend_name: Name of the backend to search.
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

                root: Sub-path to search within the backend (default "" = entire
                    library). E.g. "2024/2024-07" to restrict to one partition.
            """
            backend = self._require_backend(backend_name)
            args: dict[str, Any] = {"root": root}
            if filters is not None:
                args["filters"] = filters

            fields = await self._get_fields(backend)

            try:
                result = await self._agent.call_tool(
                    "wally", "search_photos", args, backend, progress_ctx=ctx
                )
            except AgentError as exc:
                _log.error("search_photos(%r) failed: %s", backend_name, exc)
                return {"error": str(exc)}
            # Store matches server-side; return only a token so Claude never
            # echoes the full payload back as browse_gallery arguments.
            matches: list[Any] = result.get("matches", [])  # type: ignore[union-attr]
            token = self._sessions.create(backend_name, matches, self.http_port)
            return {
                **self._search_stats(matches, fields),
                "session_token": token,
            }

        @mcp.tool(app=AppConfig(resource_uri=_GALLERY_URI))
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

            merged_token, data = self._sessions.merge(session_tokens, query_summary, self.http_port)
            return {
                "matches": data["matches"],
                "backend": data["backend"],
                "querySummary": query_summary,
                "httpPort": self.http_port,
                "sessionToken": merged_token,
                "galleryUrl": f"http://127.0.0.1:{self.http_port}/gallery/{merged_token}",
            }

        @mcp.tool()
        async def share_photos_with_claude(
            session_token: str,
            content_hashes: list[str],
        ) -> list[TextContent | ResourceLink]:
            """Share selected photos from the gallery into the Claude conversation.

            Returns resource links that Claude Desktop resolves via resources/read.
            Maximum 10 photos per share action.

            Args:
                session_token: sessionToken value from browse_gallery.
                content_hashes: List of contentHash values of the photos to share.
            """
            if not content_hashes:
                raise ValueError("No photos selected.")
            if len(content_hashes) > 10:
                raise ValueError("Cannot share more than 10 photos at a time.")

            session = self._sessions.get(session_token)
            if session is None:
                raise ValueError(
                    f"Unknown session token: {session_token!r}. Call browse_gallery first."
                )

            matches = self._sessions.get_matches_by_hashes(session_token, content_hashes)
            if not matches:
                raise ValueError("None of the requested photos were found in this session.")

            content: list[TextContent | ResourceLink] = [
                TextContent(type="text", text=f"Sharing {len(matches)} photo(s) from the gallery:"),
            ]
            for match in matches:
                content_hash = match["contentHash"]
                filename = match.get("filename", content_hash)
                content.append(
                    ResourceLink(
                        type="resource_link",
                        uri=f"photos://preview/{session_token}/{content_hash}",
                        mimeType="image/jpeg",
                        name=filename,
                    )
                )
                parts: list[str] = []
                date_taken = match.get("dateTaken")
                if date_taken:
                    parts.append(str(date_taken)[:10])
                camera = " ".join(filter(None, [match.get("make"), match.get("model")]))
                if camera:
                    parts.append(camera)
                tags = match.get("tags")
                if tags:
                    parts.append(f"tags: {', '.join(tags)}")
                meta_line = filename + (" • " + " • ".join(parts) if parts else "")
                content.append(TextContent(type="text", text=meta_line))

            return content

    # ------------------------------------------------------------------
    # Gallery resource
    # ------------------------------------------------------------------

    def _register_gallery_resource(self) -> None:
        origin = f"http://127.0.0.1:{self.http_port}"

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
            return get_gallery_html(self.http_port)

    # ------------------------------------------------------------------
    # Photo preview resource
    # ------------------------------------------------------------------

    def _register_photo_resource(self) -> None:
        agent = self._agent
        sessions = self._sessions

        @self.mcp.resource("photos://preview/{session_token}/{content_hash}")
        async def photo_preview_resource(session_token: str, content_hash: str) -> bytes:
            """Serve a photo preview JPEG on demand via the MCP resource protocol."""
            session = sessions.get(session_token)
            if session is None:
                raise ValueError(f"Unknown session token: {session_token!r}")

            matches = sessions.get_matches_by_hashes(session_token, [content_hash])
            if not matches:
                raise ValueError(f"Photo {content_hash!r} not found in session.")

            match = matches[0]
            partition = match["partition"]
            backend_name = session["backend"].split(", ")[0]

            wally_port = agent.get_wally_http_port(backend_name)
            wally_token = agent.get_wally_token(backend_name)
            if wally_port is None:
                raise RuntimeError(f"Wally is not available for backend {backend_name!r}")

            url = (
                f"http://127.0.0.1:{wally_port}/previews"
                f"/{backend_name}/{partition}/{content_hash}.jpg"
            )
            headers: dict[str, str] = {}
            if wally_token:
                headers["Authorization"] = f"Bearer {wally_token}"

            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=120.0)
                resp.raise_for_status()
                return resp.content

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
            "count": len(matches),
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

    async def _get_fields(self, backend: BackendConfig) -> list[Any]:
        """Return field definitions for a backend, fetching from Wally on first call.

        The result is cached per backend name for the lifetime of the server.
        Returns an empty list if the agent call fails.
        """
        if backend.name not in self._backend_fields:
            try:
                result = await self._agent.call_tool("wally", "list_search_fields", {}, backend)
                self._backend_fields[backend.name] = result.get("fields", [])  # type: ignore[union-attr]
            except AgentError as exc:
                _log.warning(
                    "list_search_fields failed for %r, stats will be empty: %s",
                    backend.name,
                    exc,
                )
                return []
        return self._backend_fields[backend.name]

    def _require_backend(self, name: str) -> BackendConfig:
        backend = self.config.get_backend(name)
        if backend is None:
            raise ValueError(f"Backend {name!r} not found. Use add_backend to register it first.")
        return backend
