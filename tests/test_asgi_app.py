"""Tests for the combined ASGI app that only a *real* gallery/MCP app can exercise.

``asgi_server.build_http_asgi_app``'s own middleware stack (bearer auth, CORS,
host checking, gallery-static exemption, activity tracking) is already unit
tested against lightweight stub apps in ``test_asgi_server.py`` — no need to
repeat that here with heavier real components. What's left is behavior that
depends on the *real* gallery app (``http_server.build_gallery_app``'s own
``/healthz``, ``/keepalive``, ``/shutdown`` routes) or a real ``FastMCP``
instance (the ``/mcp`` double-mount regression, which is specific to how
FastMCP's own ``http_app()`` registers routes — a stub Starlette app can't
reproduce it). None of this depends on ``McpServer`` or any Woof tool.
"""

from __future__ import annotations

import time

import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

from woof.asgi_server import LoopbackEndpoint, bind_loopback_endpoint, build_http_asgi_app
from woof.discovery import ActivityTracker
from woof.gallery_session_manager import GallerySessionManager
from woof.http_server import build_gallery_app
from woof.indexing_session_manager import IndexingSessionManager


@pytest.fixture()
def endpoint() -> LoopbackEndpoint:
    """A real bound loopback endpoint — mirrors what __main__.py binds.

    TestClient never touches the actual socket (it drives the ASGI app
    in-process), but the port must still be real and consistent between the
    allowed hosts and the Host header TestClient sends, since
    HostOriginGuard checks one against the other.
    """
    return bind_loopback_endpoint()


class _FakeShutdownHandle:
    def __init__(self) -> None:
        self.requested = False

    def request(self) -> None:
        self.requested = True


def _build_app(
    endpoint: LoopbackEndpoint,
    *,
    token: str = "secret",
    tracker: ActivityTracker | None = None,
    handle=None,
):
    """Replicates __main__.py::_run's ASGI stack assembly for testing.

    Builds a bare FastMCP app (no tools registered) alongside the real
    gallery app, rather than a full ``McpServer`` — this composition doesn't
    depend on any specific Woof tool.
    """
    tracker = tracker or ActivityTracker()
    handle = handle or _FakeShutdownHandle()
    mcp_app = FastMCP("test").http_app(path="/")
    gallery_app = build_gallery_app(
        GallerySessionManager(),
        None,
        endpoint.url,
        indexing_session_manager=IndexingSessionManager(),
        token=token,
        activity_tracker=tracker,
        shutdown_handle=handle,
    )
    return build_http_asgi_app(
        mcp_app=mcp_app,
        gallery_app=gallery_app,
        token=token,
        allowed_hosts=endpoint.allowed_hosts,
        activity_tracker=tracker,
    )


def test_healthz_requires_bearer_token(endpoint: LoopbackEndpoint) -> None:
    app = _build_app(endpoint)
    with TestClient(app, base_url=f"http://localhost:{endpoint.port}") as client:
        unauth = client.get("/healthz")
        assert unauth.status_code == 401

        ok = client.get("/healthz", headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200


def test_keepalive_touches_activity_tracker(endpoint: LoopbackEndpoint) -> None:
    tracker = ActivityTracker()
    app = _build_app(endpoint, tracker=tracker)
    with TestClient(app, base_url=f"http://localhost:{endpoint.port}") as client:
        tracker.touch()
        time.sleep(0.05)
        idle_before = tracker.idle_seconds()
        resp = client.post("/keepalive", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200
        assert tracker.idle_seconds() < idle_before


def test_shutdown_route_invokes_handle(endpoint: LoopbackEndpoint) -> None:
    handle = _FakeShutdownHandle()
    app = _build_app(endpoint, handle=handle)
    with TestClient(app, base_url=f"http://localhost:{endpoint.port}") as client:
        resp = client.post("/shutdown", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200
        assert handle.requested is True


def test_authenticated_initialize_reaches_mounted_mcp_app_at_trailing_slash(
    endpoint: LoopbackEndpoint,
) -> None:
    """Regression test for a double-mount bug: `self.mcp.http_app(path="/mcp")`
    registered the inner app's own route at "/mcp" too, so after the outer
    `Mount("/mcp", ...)` stripped its prefix, nothing matched and `/mcp/`
    404'd. Building the inner app with `path="/"` instead lines it up with
    what's left after the outer strip.

    Bare "/mcp" (no trailing slash) 404s here rather than 307-redirecting —
    confirmed in isolation to be a Starlette quirk of having a second
    `Mount("/", app=gallery_app)` alongside `Mount("/mcp", ...)`, unrelated to
    our own code. Not a functional regression: the bridge always requests the
    canonical "/mcp/" form directly (see bridge.py), never the bare path.
    """
    app = _build_app(endpoint)
    init_payload = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    headers = {
        "Authorization": "Bearer secret",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(app, base_url=f"http://localhost:{endpoint.port}") as client:
        bare = client.post("/mcp", json=init_payload, headers=headers, follow_redirects=False)
        assert bare.status_code == 404

        resp = client.post("/mcp/", json=init_payload, headers=headers)
        assert resp.status_code == 200
