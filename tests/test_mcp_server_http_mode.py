"""Tests for the HTTP-mode combined app (MCP + gallery + security middleware).

This composition lives inline in ``__main__.py::_run_http`` (mirroring how
Wally's ``__main__.py`` assembles its own ASGI stack) rather than as a method
on ``McpServer`` — so these tests replicate the same small assembly from the
same public building blocks (``McpServer.mcp``, ``http_server.build_gallery_app``,
``security.*``) instead of importing ``woof.__main__`` directly, which would
trigger real socket binding and config loading as a module-level side effect.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from woof.config import LibraryConfig, WoofConfig
from woof.discovery import ActivityTracker
from woof.http_server import build_gallery_app, with_permissive_cors
from woof.mcp_server import McpServer
from woof.security import ActivityMiddleware, BearerGuard, HostOriginGuard, allowed_hosts_for_port


@pytest.fixture()
def config(tmp_path: Path) -> WoofConfig:
    return WoofConfig(
        libraries=[LibraryConfig(name="testlib", type="local", path=str(tmp_path))],
        config_dir=tmp_path / ".woof",
    )


class _FakeShutdownHandle:
    def __init__(self) -> None:
        self.requested = False

    def request(self) -> None:
        self.requested = True


def _build_app(server: McpServer, *, tracker: ActivityTracker | None = None, handle=None):
    """Replicates __main__.py::_run_http's ASGI stack assembly for testing."""
    assert server._token is not None
    tracker = tracker or ActivityTracker()
    handle = handle or _FakeShutdownHandle()
    mcp_app = server.mcp.http_app(path="/")
    gallery_app = build_gallery_app(
        server._sessions,
        server._wally_connection,
        server.server_url,
        indexing_session_manager=server._indexing_sessions,
        token=server._token,
        activity_tracker=tracker,
        shutdown_handle=handle,
    )
    combined = Starlette(
        routes=[Mount("/mcp", app=mcp_app), Mount("/", app=gallery_app)],
        lifespan=mcp_app.router.lifespan_context,
    )
    combined = ActivityMiddleware(combined, tracker=tracker)
    combined = BearerGuard(
        combined, token=server._token, exempt_path_prefixes=("/gallery-static/",)
    )
    combined = HostOriginGuard(combined, allowed_hosts=allowed_hosts_for_port(server._port))
    return with_permissive_cors(combined)


def test_build_app_requires_a_token(config: WoofConfig) -> None:
    server = McpServer(config)  # default stdio, no token
    with pytest.raises(AssertionError):
        _build_app(server)


def test_stdio_mode_tool_results_have_no_server_token(config: WoofConfig) -> None:
    server = McpServer(config)
    assert server._token is None


def test_http_mode_server_urls_and_token_set(config: WoofConfig) -> None:
    server = McpServer(config, transport="http", token="secret")
    assert server.server_urls[0].startswith("http://localhost:")
    assert server._token == "secret"


def test_healthz_requires_bearer_token(config: WoofConfig) -> None:
    server = McpServer(config, transport="http", token="secret")
    app = _build_app(server)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        unauth = client.get("/healthz")
        assert unauth.status_code == 401

        ok = client.get("/healthz", headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200


def test_rejected_response_still_carries_cors_header(config: WoofConfig) -> None:
    """Regression test: CORSMiddleware must wrap outside BearerGuard/HostOriginGuard,
    not inside — otherwise a 401/403 response never reaches it and browsers
    surface a misleading "blocked by CORS policy" error instead of the real
    auth/host failure.
    """
    server = McpServer(config, transport="http", token="secret")
    app = _build_app(server)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.get("/healthz", headers={"Origin": "https://example.claudemcpcontent.com"})
        assert resp.status_code == 401
        assert resp.headers.get("access-control-allow-origin") == "*"


def test_cors_preflight_for_authorization_header_succeeds(config: WoofConfig) -> None:
    """Regression test: CORSMiddleware's Starlette defaults are allow_methods=("GET",)
    and allow_headers=() — too narrow now that the gallery frontend sends POST
    (cancel/keepalive) and an Authorization header (bearer token), both of
    which trigger a preflight OPTIONS the browser requires a 200 for before
    it will even attempt the real request.
    """
    server = McpServer(config, transport="http", token="secret")
    app = _build_app(server)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.options(
            "/api/results/tok",
            headers={
                "Origin": "https://example.claudemcpcontent.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-headers") == "authorization"


def test_gallery_static_is_exempt_from_bearer_auth(config: WoofConfig) -> None:
    """`<script src>`/`<link href>` tags loading the compiled JS/CSS bundle can't
    attach a bearer token at all, and the bundle carries no user data — so this
    path must be reachable without auth (HostOriginGuard still applies).
    """
    server = McpServer(config, transport="http", token="secret")
    app = _build_app(server)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.get("/gallery-static/nonexistent.js")
        # 404 (no such asset) rather than 401 (blocked by auth) proves the
        # exemption let the request reach the StaticFiles route at all.
        assert resp.status_code == 404


def test_wrong_host_header_is_rejected_even_with_valid_token(config: WoofConfig) -> None:
    server = McpServer(config, transport="http", token="secret")
    app = _build_app(server)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.get(
            "/healthz",
            headers={"Authorization": "Bearer secret", "Host": "evil.example.com"},
        )
        assert resp.status_code == 403


def test_keepalive_touches_activity_tracker(config: WoofConfig) -> None:
    server = McpServer(config, transport="http", token="secret")
    tracker = ActivityTracker()
    app = _build_app(server, tracker=tracker)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        tracker.touch()
        time.sleep(0.05)
        idle_before = tracker.idle_seconds()
        resp = client.post("/keepalive", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200
        assert tracker.idle_seconds() < idle_before


def test_shutdown_route_invokes_handle(config: WoofConfig) -> None:
    server = McpServer(config, transport="http", token="secret")
    handle = _FakeShutdownHandle()
    app = _build_app(server, handle=handle)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.post("/shutdown", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200
        assert handle.requested is True


def test_mcp_mounted_at_slash_mcp_requires_auth(config: WoofConfig) -> None:
    server = McpServer(config, transport="http", token="secret")
    app = _build_app(server)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.post("/mcp", json={})
        assert resp.status_code == 401


def test_authenticated_initialize_reaches_mounted_mcp_app_at_trailing_slash(
    config: WoofConfig,
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
    server = McpServer(config, transport="http", token="secret")
    app = _build_app(server)
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
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        bare = client.post("/mcp", json=init_payload, headers=headers, follow_redirects=False)
        assert bare.status_code == 404

        resp = client.post("/mcp/", json=init_payload, headers=headers)
        assert resp.status_code == 200
