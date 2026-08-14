"""Generic ASGI/Starlette/uvicorn plumbing — no Woof route or business logic.

``http_server.py`` owns Woof's actual routes/handlers (gallery, media proxy,
API endpoints); ``__main__.py`` owns Woof-lifecycle wiring (discovery file,
idle-shutdown, signal-ready). This module is the one place that knows how to
turn an ASGI app into a running uvicorn server, and how to compose the MCP
app + gallery app + security middleware into one combined app for HTTP mode.
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount
from starlette.types import ASGIApp

from .security import ActivityMiddleware, BearerGuard, HostOriginGuard

if TYPE_CHECKING:
    from .discovery import ActivityTracker


@dataclass
class LoopbackEndpoint:
    """A pre-bound loopback socket, plus the URLs used to reach it.

    Shared by both the MCP app (CSP resource/connect domains, gallery URLs
    embedded in tool results) and the gallery/media app (embedded in the
    gallery HTML) — a single bind is the one source of truth for "which port
    did we actually get", computed before any of those consumers exist.

    ``urls`` lists both loopback hostnames since different MCP hosts accept
    different ones in their iframe CSP (Claude Desktop Chat requires
    "localhost"; Claude CoWork blocks it and requires "127.0.0.1") — the
    gallery frontend tries each in order.
    """

    sock: socket.socket
    port: int
    urls: list[str]

    @property
    def url(self) -> str:
        return self.urls[0]

    @property
    def allowed_hosts(self) -> set[str]:
        """``Host`` header values that should be accepted (see ``security.HostOriginGuard``)."""
        return {url.removeprefix("http://") for url in self.urls}


def bind_loopback_endpoint() -> LoopbackEndpoint:
    """Bind an OS-assigned TCP port on 127.0.0.1.

    Left unbound-but-listening (not yet passed to a server) so the caller can
    learn the port synchronously before starting anything async.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    return LoopbackEndpoint(
        sock=sock,
        port=port,
        urls=[f"http://localhost:{port}", f"http://127.0.0.1:{port}"],
    )


def with_permissive_cors(app: ASGIApp) -> ASGIApp:
    """Wrap *app* with the permissive CORS config the gallery frontend needs.

    allow_methods/allow_headers default to GET-only / none in Starlette — too
    narrow now that the gallery frontend sends POST (cancel/keepalive) and an
    Authorization header (bearer token), both of which trigger a preflight
    OPTIONS request that CORSMiddleware itself must answer with 200 before
    the browser will even attempt the real request.
    """
    return CORSMiddleware(app, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def build_http_asgi_app(
    *,
    mcp_app: Starlette,
    gallery_app: Starlette,
    token: str,
    allowed_hosts: set[str],
    activity_tracker: ActivityTracker,
    gallery_sessions: Any | None = None,
    indexing_sessions: Any | None = None,
) -> ASGIApp:
    """Combine the MCP app and gallery app into one authenticated ASGI app.

    Mounts *mcp_app* at ``/mcp`` and *gallery_app* at ``/`` under one
    Starlette app (carrying *mcp_app*'s own lifespan, so its startup/shutdown
    still runs), then layers security middleware innermost-to-outermost:
    ``ActivityMiddleware`` (idle-shutdown tracking) → ``BearerGuard`` (auth,
    exempting ``/gallery-static/`` — `<script src>`/`<link href>` tags can't
    attach a token, and the compiled bundle carries no user data) →
    ``HostOriginGuard`` (DNS-rebinding mitigation) → CORS (outermost, so even
    a rejected 401/403 response still carries CORS headers — otherwise
    browsers surface a misleading "blocked by CORS policy" error instead of
    the real auth/host cause).

    *mcp_app* must have been built with ``self.mcp.http_app(path="/")`` —
    NOT ``path="/mcp"`` — since it's mounted at ``/mcp`` here, which strips
    that prefix before dispatching. Registering the inner app's own route at
    ``/mcp`` too would double it up (Mount strips ``/mcp``, leaving ``/``,
    which wouldn't match an inner route still expecting ``/mcp``).
    """
    # FastMCP's http_app() returns a StarletteWithLifespan subclass exposing a
    # convenience `.lifespan` property, but we only depend on plain Starlette
    # here — `.router.lifespan_context` is the same underlying value and is
    # part of base Starlette's public API.
    combined: ASGIApp = Starlette(
        routes=[Mount("/mcp", app=mcp_app), Mount("/", app=gallery_app)],
        lifespan=mcp_app.router.lifespan_context,
    )
    combined = ActivityMiddleware(combined, tracker=activity_tracker)
    combined = BearerGuard(
        combined,
        token=token,
        exempt_path_prefixes=("/gallery-static/",),
        gallery_sessions=gallery_sessions,
        indexing_sessions=indexing_sessions,
    )
    combined = HostOriginGuard(combined, allowed_hosts=allowed_hosts)
    return with_permissive_cors(combined)


def make_uvicorn_server(
    app: ASGIApp,
    endpoint: LoopbackEndpoint,
    *,
    log_level: str = "warning",
    access_log: bool = False,
    install_signal_handlers: bool = True,
    ready: threading.Event | None = None,
) -> uvicorn.Server:
    """Build a ``uvicorn.Server`` for *app*, bound to *endpoint*, without starting it.

    The returned server always serves on *endpoint*'s pre-bound socket —
    callers just await ``.serve()`` with no ``sockets=`` argument, so the
    socket/port stays owned by the server rather than threaded through by
    each caller.

    ``install_signal_handlers=False`` preserves a compatibility override for
    uvicorn releases where ``Server.install_signal_handlers()`` was a real
    overridable method controlling whether SIGINT/SIGTERM get registered —
    needed when uvicorn shares an event loop or daemon thread it doesn't own
    (the test-only standalone gallery server,
    ``tests/http_test_server.py::start_http_server``), so the main thread
    retains control. On current uvicorn (>=0.29ish), signal
    capture instead happens via ``Server.capture_signals()``, which already
    checks ``threading.current_thread() is threading.main_thread()`` itself
    and skips real registration off the main thread automatically — so this
    flag is inert there, but harmless, and keeps the same call shape if an
    older uvicorn is ever installed. Production's combined server runs on the
    main thread via ``asyncio.run`` and keeps the default ``True``.

    ``ready``, if given, is set once uvicorn's ``startup()`` completes —
    useful for a caller on another thread waiting for the socket to actually
    be accepting connections before proceeding.
    """

    class _Server(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            if install_signal_handlers:
                super().install_signal_handlers()

        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets or [endpoint.sock])
            if ready is not None:
                ready.set()

    config = uvicorn.Config(app, log_level=log_level, access_log=access_log)
    return _Server(config)


async def serve_with_ready(
    app: ASGIApp,
    endpoint: LoopbackEndpoint,
    ready: threading.Event | None = None,
    *,
    log_level: str = "warning",
    access_log: bool = False,
) -> None:
    """Serve *app* on *endpoint*'s pre-bound socket until cancelled or stopped.

    Never installs its own signal handlers — intended for a server sharing an
    event loop or daemon thread it doesn't own (the test-only standalone
    gallery server), where the main thread must retain control of
    SIGINT/SIGTERM.
    """
    server = make_uvicorn_server(
        app,
        endpoint,
        log_level=log_level,
        access_log=access_log,
        install_signal_handlers=False,
        ready=ready,
    )
    await server.serve()
