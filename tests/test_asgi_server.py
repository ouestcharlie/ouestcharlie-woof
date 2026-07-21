"""Tests for woof.asgi_server: generic ASGI/Starlette/uvicorn plumbing."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from woof.asgi_server import (
    build_http_asgi_app,
    make_uvicorn_server,
    serve_with_ready,
    with_permissive_cors,
)
from woof.discovery import ActivityTracker


async def _ok(request):
    return JSONResponse({"status": "ok"})


def _trivial_app() -> Starlette:
    return Starlette(routes=[Route("/thing", _ok)])


# ---------------------------------------------------------------------------
# with_permissive_cors
# ---------------------------------------------------------------------------


def test_with_permissive_cors_answers_preflight_for_authorization_header() -> None:
    client = TestClient(with_permissive_cors(_trivial_app()))
    resp = client.options(
        "/thing",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-headers") == "authorization"


def test_with_permissive_cors_allows_post() -> None:
    app = Starlette(routes=[Route("/thing", _ok, methods=["POST"])])
    client = TestClient(with_permissive_cors(app))
    resp = client.options(
        "/thing",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# build_http_asgi_app
# ---------------------------------------------------------------------------


def _build(*, token: str = "secret", allowed_hosts=None):
    mcp_app = Starlette(routes=[Route("/", _ok)])
    gallery_app = Starlette(
        routes=[
            Route("/thing", _ok),
            Route("/gallery-static/x", _ok),
        ]
    )
    return build_http_asgi_app(
        mcp_app=mcp_app,
        gallery_app=gallery_app,
        token=token,
        allowed_hosts=allowed_hosts if allowed_hosts is not None else {"testserver"},
        activity_tracker=ActivityTracker(),
    )


def test_mcp_app_reachable_at_mcp_prefix_with_auth() -> None:
    client = TestClient(_build())
    resp = client.get("/mcp/", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200


def test_gallery_app_reachable_at_root_with_auth() -> None:
    client = TestClient(_build())
    resp = client.get("/thing", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200


def test_gallery_static_exempt_from_auth() -> None:
    client = TestClient(_build())
    resp = client.get("/gallery-static/x")
    assert resp.status_code == 200


def test_unauthenticated_request_rejected_with_cors_header() -> None:
    client = TestClient(_build())
    resp = client.get("/thing", headers={"Origin": "https://example.com"})
    assert resp.status_code == 401
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_wrong_host_rejected() -> None:
    client = TestClient(_build(allowed_hosts={"127.0.0.1:9999"}))
    resp = client.get("/thing", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 403


def test_activity_tracker_touched_on_request() -> None:
    tracker = ActivityTracker()
    mcp_app = Starlette(routes=[Route("/", _ok)])
    gallery_app = Starlette(routes=[Route("/thing", _ok)])
    app = build_http_asgi_app(
        mcp_app=mcp_app,
        gallery_app=gallery_app,
        token="secret",
        allowed_hosts={"testserver"},
        activity_tracker=tracker,
    )
    tracker.touch()
    time.sleep(0.05)
    idle_before = tracker.idle_seconds()
    TestClient(app).get("/thing", headers={"Authorization": "Bearer secret"})
    assert tracker.idle_seconds() < idle_before


# ---------------------------------------------------------------------------
# make_uvicorn_server
#
# Note: current uvicorn (0.49) has no overridable `install_signal_handlers`
# method at all — signal capture happens via `Server.capture_signals()`,
# which already detects non-main-thread and skips real registration on its
# own. `install_signal_handlers=False` is a compatibility no-op for older
# uvicorn releases where that method existed and mattered (see docstring),
# so there's nothing observable to assert against the installed version —
# covered instead by confirming construction succeeds and wires the app/config
# through correctly.
# ---------------------------------------------------------------------------


def test_make_uvicorn_server_wires_app_and_config() -> None:
    app = _trivial_app()
    server = make_uvicorn_server(app, log_level="info", access_log=True)
    assert isinstance(server, uvicorn.Server)
    assert server.config.app is app
    assert server.config.log_level == "info"
    assert server.config.access_log is True


# ---------------------------------------------------------------------------
# serve_with_ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serve_with_ready_sets_event_and_serves_requests() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    ready = threading.Event()
    task = asyncio.create_task(serve_with_ready(_trivial_app(), sock, ready))
    try:
        for _ in range(100):
            if ready.is_set():
                break
            await asyncio.sleep(0.02)
        assert ready.is_set()

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{port}/thing")
            assert resp.status_code == 200
    finally:
        # Reach into the server via the task's underlying coroutine isn't
        # exposed, so cancel — serve_with_ready has no other shutdown handle
        # in this test; cancellation is the standard way to stop uvicorn.serve().
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
