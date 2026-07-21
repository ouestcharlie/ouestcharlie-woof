"""Tests for WoofServer's HTTP-mode combined app (OEC-27)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from woof.config import LibraryConfig, WoofConfig
from woof.discovery import ActivityTracker
from woof.server import WoofServer


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


def _build_app(server: WoofServer, *, tracker: ActivityTracker | None = None, handle=None):
    return server.build_http_app(
        activity_tracker=tracker or ActivityTracker(),
        shutdown_handle=handle or _FakeShutdownHandle(),
    )


def test_build_http_app_requires_http_transport_and_token(config: WoofConfig) -> None:
    server = WoofServer(config)  # default stdio, no token
    with pytest.raises(RuntimeError):
        _build_app(server)


def test_stdio_mode_tool_results_have_no_server_token(config: WoofConfig) -> None:
    server = WoofServer(config)
    assert server._token is None


def test_http_mode_server_urls_and_token_set(config: WoofConfig) -> None:
    server = WoofServer(config, transport="http", token="secret")
    assert server.server_urls[0].startswith("http://localhost:")
    assert server._token == "secret"


def test_healthz_requires_bearer_token(config: WoofConfig) -> None:
    server = WoofServer(config, transport="http", token="secret")
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
    server = WoofServer(config, transport="http", token="secret")
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
    server = WoofServer(config, transport="http", token="secret")
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
    server = WoofServer(config, transport="http", token="secret")
    app = _build_app(server)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.get("/gallery-static/nonexistent.js")
        # 404 (no such asset) rather than 401 (blocked by auth) proves the
        # exemption let the request reach the StaticFiles route at all.
        assert resp.status_code == 404


def test_wrong_host_header_is_rejected_even_with_valid_token(config: WoofConfig) -> None:
    server = WoofServer(config, transport="http", token="secret")
    app = _build_app(server)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.get(
            "/healthz",
            headers={"Authorization": "Bearer secret", "Host": "evil.example.com"},
        )
        assert resp.status_code == 403


def test_keepalive_touches_activity_tracker(config: WoofConfig) -> None:
    server = WoofServer(config, transport="http", token="secret")
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
    server = WoofServer(config, transport="http", token="secret")
    handle = _FakeShutdownHandle()
    app = _build_app(server, handle=handle)
    with TestClient(app, base_url=f"http://localhost:{server._port}") as client:
        resp = client.post("/shutdown", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200
        assert handle.requested is True


def test_mcp_mounted_at_slash_mcp_requires_auth(config: WoofConfig) -> None:
    server = WoofServer(config, transport="http", token="secret")
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
    404'd — only the bare, unauthenticated-friendly `/mcp` 307-redirected.
    `build_http_app` now builds the inner app with `path="/"` so it lines up
    with what's left after the outer strip.
    """
    server = WoofServer(config, transport="http", token="secret")
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
        # Bare "/mcp" still 307-redirects (inherent Starlette Mount behavior) —
        # the bridge requests the trailing-slash form directly to avoid this.
        bare = client.post("/mcp", json=init_payload, headers=headers, follow_redirects=False)
        assert bare.status_code == 307

        resp = client.post("/mcp/", json=init_payload, headers=headers)
        assert resp.status_code == 200
