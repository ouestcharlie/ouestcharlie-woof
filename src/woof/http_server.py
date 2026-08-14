"""Local HTTP server for proxying media requests and serving the gallery.

Runs on 127.0.0.1 with an OS-assigned port backed by Starlette + uvicorn (async
ASGI). In production, ``build_gallery_app``'s app is mounted alongside the MCP
app under one combined app (see ``asgi_server.build_http_asgi_app``, driven by
``__main__.py``) — all async work on a single shared loop, no cross-thread
bridging. Tests that need this app served standalone (no MCP app) should use
``tests/http_test_server.py::start_http_server`` instead of adding a
production entry point for it.

URL scheme — every session-scoped route carries its session id as the second
path segment, which both authenticates and scopes the request:
  GET  /gallery/{session_id}/html                    — gallery HTML
  GET  /gallery/{session_id}/results                 — JSON session data (matches + metadata)
  GET  /gallery/{session_id}/results/page/{page}     — load 0-indexed Wally page into session
  GET  /gallery/{session_id}/media/thumbnail/{library}/{partition}/{avif_hash}       (to Wally)
  GET  /gallery/{session_id}/media/previews/{library}/{partition}/{content_hash}.jpg (to Wally)
  GET  /gallery/{session_id}/media/video/{library}/{partition}/{content_hash}.mp4    (Wally, Range)
  GET  /indexing/{session_id}/html                   — indexing progress HTML
  GET  /indexing/{session_id}/status                 — JSON indexing session state
  POST /indexing/{session_id}/cancel                 — request indexing cancellation
  GET  /gallery-static/{path}                   — gallery JS/CSS assets from dist/

where {partition} may contain slashes (e.g. "2024/2024-07").
All library media (thumbnails, previews and video) is served by Wally and proxied
here. Media is scoped to a gallery session: {session_id} names the session and the file
must be one of its matches, else 403. The proxy streams bodies and forwards
Range/Content-Range so <video> seeking works without buffering GB-scale files
(OEC-39a §2).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .discovery import ActivityTracker
from .gallery_session_manager import GallerySessionManager, PageOutOfRange

_log = logging.getLogger(__name__)

# Pre-built Svelte app (produced by `npm run build` in gallery/)
_GALLERY_DIST_DIR = Path(__file__).parent / "gallery" / "dist"
_GALLERY_DIST_HTML = _GALLERY_DIST_DIR / "index.html"


def _session_matches(session: Any) -> list[dict[str, Any]]:
    """All match records reachable from *session*, including chained sub-sessions.

    A merged (chained) session keeps its first page in ``matches`` and the rest in
    ``chainedSessions``; the union is the set of files the user was shown.
    """
    collected = list(session.matches)
    for sub in getattr(session, "chainedSessions", []) or []:
        collected.extend(sub.matches)
    return collected


def _media_in_session(session: Any, library: str, rest: str) -> bool:
    """Whether a ``/media`` request names a file in *session* (per-session scope check).

    ``rest`` is ``{partition}/{hash}[.ext]`` where ``partition`` may contain
    slashes, so the hash is the last path segment with any extension stripped and
    the partition is everything before it. The stem must be either the ``avifHash``
    (thumbnail grid, shared by colocated photos) or the ``contentHash`` (preview,
    video) of an in-session match in the same library and partition — matching
    against both is robust to the ``kind`` label and safe (the two hash spaces do
    not collide).
    """
    parts = rest.split("/")
    filename = parts[-1]
    partition = "/".join(parts[:-1])
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return any(
        match.get("library") == library
        and match.get("partition") == partition
        and stem in (match.get("avifHash"), match.get("contentHash"))
        for match in _session_matches(session)
    )


def get_gallery_html(server_url: str) -> str:
    """Return the gallery HTML with asset URLs rewritten to absolute server URLs.

    Vite builds the app with base='/gallery-static/'.  At runtime we replace
    those relative-rooted paths with {server_url}/gallery-static/ so the MCP
    Apps iframe (and direct browser access) can load JS/CSS.

    Nothing else is embedded. The frontend derives its candidate origins from the
    tool result (MCP host path) or ``location.origin`` (direct access), and its
    session id from the tool result or the URL path (``/gallery/{session_id}/html``).
    """
    if _GALLERY_DIST_HTML.exists():
        html = _GALLERY_DIST_HTML.read_text(encoding="utf-8")
        return html.replace("/gallery-static/", f"{server_url}/gallery-static/")
    return _gallery_placeholder()


def build_gallery_app(
    session_manager: GallerySessionManager,
    wally_connection_fn: Any | None,
    server_url: str,
    indexing_session_manager: Any | None = None,
    *,
    token: str | None = None,
    activity_tracker: ActivityTracker | None = None,
    shutdown_handle: Any | None = None,
) -> Starlette:
    """Build the gallery/media/lifecycle Starlette app

    Args:
        token: HTTP mode's master bearer token. No longer embedded in the gallery
            HTML — each gallery is served with its own session id instead —
            retained for callers and potential lifecycle use.
        activity_tracker: touched by the ``/keepalive`` route.
        shutdown_handle: object with a ``.request()`` method invoked by
            ``POST /shutdown``.
    """

    async def gallery_html(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        session = session_manager.get(session_id)
        if session is None:
            return Response(status_code=404)
        # The frontend reads its session id from this page's URL path, so nothing
        # session-specific needs embedding here.
        html = get_gallery_html(server_url)
        return HTMLResponse(
            html,
            headers={"Content-Security-Policy": f"default-src 'self' {server_url}"},
        )

    async def api_results(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        session = session_manager.get(session_id)
        if session is None:
            return JSONResponse(
                {"error": f"Session {session_id!r} not found or expired"}, status_code=404
            )
        return JSONResponse(session.transfert_object())

    async def api_page(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        try:
            page = int(request.path_params["page"])
        except (ValueError, KeyError):
            return JSONResponse({"error": "invalid page"}, status_code=400)

        session = session_manager.get(session_id)
        if session is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        try:
            ok = await session.fetch_page(page)
        except PageOutOfRange:
            return JSONResponse({"error": "out_of_range"}, status_code=404)
        if not ok:
            return JSONResponse({"error": "fetch_failed"}, status_code=502)

        return JSONResponse(session.transfert_object())

    async def proxy_media(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        kind = request.path_params["kind"]
        library = request.path_params["library"]
        rest = request.path_params["rest"]
        # Scope the media to the gallery session: the session id in the path must name
        # a live session, and the requested file must be one of that session's matches.
        # A valid session id alone is necessary but not sufficient.
        session = session_manager.get(session_id)
        if session is None:
            return Response("Unknown gallery session", status_code=404)
        if not _media_in_session(session, library, rest):
            return Response("File not in gallery session", status_code=403)
        wally_port, wally_token = (
            wally_connection_fn(library) if wally_connection_fn is not None else (None, None)
        )
        if wally_port is None:
            return Response(
                f"Wally preview server not available for library '{library}'", status_code=503
            )
        safe = "/:@!$&'()*+,;="
        url = f"http://127.0.0.1:{wally_port}/{quote(f'{kind}/{library}/{rest}', safe=safe)}"
        headers: dict[str, str] = {}
        if wally_token:
            headers["Authorization"] = f"Bearer {wally_token}"
        # Forward Range so <video> seeking works — Wally answers with 206 and a
        # Content-Range slice, which must flow through unchanged (OEC-39a §2).
        range_header = request.headers.get("range")
        if range_header:
            headers["Range"] = range_header

        # Stream the body rather than buffering it: video responses can be
        # GB-scale, and buffering would defeat Range support. read=None disables
        # the read timeout so long streams are not truncated mid-body.
        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, read=None))
        try:
            req = client.build_request("GET", url, headers=headers)
            upstream = await client.send(req, stream=True)
        except Exception as exc:
            await client.aclose()
            _log.error("Proxy to Wally failed for %r/%r/%r: %s", kind, library, rest, exc)
            return Response(status_code=503)

        passthrough = {}
        for name in ("content-length", "content-range", "accept-ranges"):
            value = upstream.headers.get(name)
            if value is not None:
                passthrough[name] = value

        async def body_iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            body_iter(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "image/jpeg"),
            headers=passthrough,
        )

    async def indexing_html(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        if indexing_session_manager is None or indexing_session_manager.get(session_id) is None:
            return Response(status_code=404)
        # The frontend reads its session id from this page's URL path; library and
        # partition_scope come from the status endpoint.
        html = get_gallery_html(server_url)
        return HTMLResponse(
            html,
            headers={"Content-Security-Policy": f"default-src 'self' {server_url}"},
        )

    async def api_indexing(request: Request) -> Response:
        if indexing_session_manager is None:
            return JSONResponse({"error": "not configured"}, status_code=503)
        sid = request.path_params["session_id"]
        session = indexing_session_manager.get(sid)
        if session is None:
            return JSONResponse({"error": f"Session {sid!r} not found"}, status_code=404)
        return JSONResponse(session)

    async def api_indexing_cancel(request: Request) -> Response:
        if indexing_session_manager is None:
            return JSONResponse({"error": "not configured"}, status_code=503)
        sid = request.path_params["session_id"]
        ok = indexing_session_manager.cancel(sid)
        if not ok:
            return JSONResponse({"error": "not cancellable"}, status_code=409)
        return JSONResponse({"status": "cancelling"})

    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def keepalive(request: Request) -> Response:
        if activity_tracker is not None:
            activity_tracker.touch()
        return JSONResponse({"status": "ok"})

    async def shutdown(request: Request) -> Response:
        if shutdown_handle is None:
            return JSONResponse({"error": "not supported"}, status_code=503)
        shutdown_handle.request()
        return JSONResponse({"status": "stopping"})

    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/keepalive", keepalive, methods=["POST"]),
        Route("/shutdown", shutdown, methods=["POST"]),
        Route("/gallery/{session_id}/html", gallery_html),
        Route("/gallery/{session_id}/results/page/{page}", api_page),
        Route("/gallery/{session_id}/results", api_results),
        Route("/gallery/{session_id}/media/{kind}/{library}/{rest:path}", proxy_media),
        Route("/indexing/{session_id}/html", indexing_html),
        Route("/indexing/{session_id}/cancel", api_indexing_cancel, methods=["POST"]),
        Route("/indexing/{session_id}/status", api_indexing),
        Mount("/gallery-static", StaticFiles(directory=str(_GALLERY_DIST_DIR), check_dir=False)),
    ]
    return Starlette(routes=routes)


def _gallery_placeholder() -> str:
    """Placeholder served when the gallery bundle has not been built yet."""
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
        "<title>OuEstCharlie Gallery</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#111;color:#888;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
        "</style></head><body>"
        "<p>Gallery not built. Run <code>npm run build</code> in <code>gallery/</code>.</p>"
        "</body></html>"
    )
