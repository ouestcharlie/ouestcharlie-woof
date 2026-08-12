"""ASGI security/lifecycle middleware for Woof's HTTP-mode server.

Independent concerns, composed together by ``__main__.py`` around the
combined MCP + gallery app:
  - BearerGuard: authenticates the caller (bridge, or gallery iframe via
    Authorization header / ``/session_id`` path segment for the gallery.
  - HostOriginGuard: rejects requests whose ``Host`` header doesn't match one
    of the loopback origins Woof itself is bound to, mitigating DNS rebinding
    (a remote page tricking a browser into resolving an attacker-controlled
    hostname to 127.0.0.1 and issuing same-origin-looking requests).
  - ActivityMiddleware: touches an ``ActivityTracker`` on every request that
    reaches it, so idle-shutdown accounts for all traffic (MCP tool calls
    included), not just explicit ``/keepalive`` pings.
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from .discovery import ActivityTracker


def _classify_session_route(path: str) -> tuple[str, str] | None:
    """Map a request path to its ``(manager, in-path session id)``, or ``None``.

    Session-scoped routes are ``/gallery/{session_id}/…`` (HTML, results, media —
    owned by the gallery session manager) and ``/indexing/{session_id}/…`` (status,
    cancel — owned by the indexing session manager); the session id is always the
    second segment, both credential and scope key. Anything else (control plane,
    ``/gallery-static/``) returns ``None``.
    """
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 2 and segments[0] in ("gallery", "indexing"):
        return (segments[0], segments[1])
    return None


class BearerGuard(BaseHTTPMiddleware):
    """Reject requests that lack a valid credential.

    Two credential classes are accepted:

    - The **master token**, as ``Authorization: Bearer <token>`` or a ``?token=``
      query parameter — used by the stdio bridge; grants every route.
    - A live **session id** carried as the second path segment of a session-scoped
      route (``/gallery/{session_id}/…`` — HTML, results, media — bound to the
      gallery manager; ``/indexing/{session_id}/…`` to the indexing manager). It
      grants only its own session's routes and never the control plane. Per-file
      media scoping is enforced separately in ``proxy_media``.

    ``exempt_path_prefixes`` skips auth entirely for matching paths — used
    for ``/gallery-static/`` (the compiled JS/CSS bundle), which `<script
    src>`/`<link href>` tags load with no way to attach a token at all, and
    which carries no user data anyway (identical bundle for every install).

    ``gallery_sessions``/``indexing_sessions`` are the two managers (anything
    exposing ``.get(token)`` returning ``None`` when absent); when omitted, only
    the master token is accepted (the standalone gallery test server passes
    neither, but also mounts no ``BearerGuard``).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        exempt_path_prefixes: tuple[str, ...] = (),
        gallery_sessions: Any | None = None,
        indexing_sessions: Any | None = None,
    ) -> None:
        super().__init__(app)
        self._token = token
        self._exempt_path_prefixes = exempt_path_prefixes
        self._gallery_sessions = gallery_sessions
        self._indexing_sessions = indexing_sessions

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(self._exempt_path_prefixes):
            return await call_next(request)
        header = request.headers.get("authorization")
        if header == f"Bearer {self._token}":
            return await call_next(request)
        if request.query_params.get("token") == self._token:
            return await call_next(request)
        if self._session_route_authorized(request.url.path):
            return await call_next(request)
        return Response("Unauthorized", status_code=401)

    def _session_route_authorized(self, path: str) -> bool:
        classified = _classify_session_route(path)
        if classified is None:
            return False
        manager_kind, session_id = classified
        manager = self._gallery_sessions if manager_kind == "gallery" else self._indexing_sessions
        return manager is not None and manager.get(session_id) is not None


class HostOriginGuard(BaseHTTPMiddleware):
    """Reject requests whose Host header isn't one of the expected loopback origins."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: set[str]) -> None:
        super().__init__(app)
        self._allowed_hosts = allowed_hosts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = request.headers.get("host", "")
        if host not in self._allowed_hosts:
            return Response("Forbidden (invalid Host header)", status_code=403)
        return await call_next(request)


class ActivityMiddleware(BaseHTTPMiddleware):
    """Touches an :class:`~woof.discovery.ActivityTracker` on every request that reaches it.

    Should be placed inside ``BearerGuard``/``HostOriginGuard`` in the
    middleware stack so only authenticated, correctly-addressed traffic
    counts as activity.
    """

    def __init__(self, app: ASGIApp, *, tracker: ActivityTracker) -> None:
        super().__init__(app)
        self._tracker = tracker

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        self._tracker.touch()
        return await call_next(request)


def allowed_hosts_for_port(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}
