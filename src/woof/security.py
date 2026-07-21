"""ASGI security middleware for Woof's HTTP-mode server (OEC-27).

Two independent concerns, both required on every route:
  - BearerGuard: authenticates the caller (bridge, or gallery iframe via
    Authorization header / ``?token=`` query param for requests — like
    ``<img src>`` — that cannot carry custom headers).
  - HostOriginGuard: rejects requests whose ``Host`` header doesn't match one
    of the loopback origins Woof itself is bound to, mitigating DNS rebinding
    (a remote page tricking a browser into resolving an attacker-controlled
    hostname to 127.0.0.1 and issuing same-origin-looking requests).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class BearerGuard(BaseHTTPMiddleware):
    """Reject requests that lack a valid bearer token.

    Accepts the token either as ``Authorization: Bearer <token>`` (used by
    the stdio bridge and by ``fetch()`` calls from the gallery frontend) or
    as a ``?token=`` query parameter (used by ``<img src>`` loads, which
    cannot set custom headers).

    ``exempt_path_prefixes`` skips auth entirely for matching paths — used
    for ``/gallery-static/`` (the compiled JS/CSS bundle), which `<script
    src>`/`<link href>` tags load with no way to attach a token at all, and
    which carries no user data anyway (identical bundle for every install).
    """

    def __init__(
        self, app: ASGIApp, *, token: str, exempt_path_prefixes: tuple[str, ...] = ()
    ) -> None:
        super().__init__(app)
        self._token = token
        self._exempt_path_prefixes = exempt_path_prefixes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(self._exempt_path_prefixes):
            return await call_next(request)
        header = request.headers.get("authorization")
        if header == f"Bearer {self._token}":
            return await call_next(request)
        if request.query_params.get("token") == self._token:
            return await call_next(request)
        return Response("Unauthorized", status_code=401)


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


def allowed_hosts_for_port(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}
