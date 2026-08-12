"""Tests for woof.security: BearerGuard and HostOriginGuard ASGI middleware."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from woof.security import BearerGuard, HostOriginGuard, allowed_hosts_for_port


async def _ok(request):
    return JSONResponse({"status": "ok"})


def _bearer_app(token: str, *, exempt_path_prefixes: tuple[str, ...] = ()) -> Starlette:
    app = Starlette(routes=[Route("/thing", _ok), Route("/public/asset", _ok)])
    return BearerGuard(app, token=token, exempt_path_prefixes=exempt_path_prefixes)


def _host_app(allowed: set[str]) -> Starlette:
    app = Starlette(routes=[Route("/thing", _ok)])
    return HostOriginGuard(app, allowed_hosts=allowed)


def test_bearer_guard_accepts_valid_authorization_header() -> None:
    client = TestClient(_bearer_app("secret"))
    resp = client.get("/thing", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200


def test_bearer_guard_accepts_valid_query_param_token() -> None:
    client = TestClient(_bearer_app("secret"))
    resp = client.get("/thing?token=secret")
    assert resp.status_code == 200


def test_bearer_guard_rejects_missing_credentials() -> None:
    client = TestClient(_bearer_app("secret"))
    resp = client.get("/thing")
    assert resp.status_code == 401


def test_bearer_guard_rejects_wrong_header_token() -> None:
    client = TestClient(_bearer_app("secret"))
    resp = client.get("/thing", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_bearer_guard_rejects_wrong_query_token() -> None:
    client = TestClient(_bearer_app("secret"))
    resp = client.get("/thing?token=wrong")
    assert resp.status_code == 401


def test_bearer_guard_exempts_matching_path_prefix_without_any_credentials() -> None:
    client = TestClient(_bearer_app("secret", exempt_path_prefixes=("/public/",)))
    resp = client.get("/public/asset")
    assert resp.status_code == 200


def test_bearer_guard_still_requires_auth_outside_exempt_prefix() -> None:
    client = TestClient(_bearer_app("secret", exempt_path_prefixes=("/public/",)))
    resp = client.get("/thing")
    assert resp.status_code == 401


class _FakeManager:
    """Minimal stand-in for a session manager: `.get(token)` → truthy if known."""

    def __init__(self, tokens: tuple[str, ...] = ()) -> None:
        self._tokens = set(tokens)

    def get(self, token: str) -> object | None:
        return {"token": token} if token in self._tokens else None


def _session_app(
    token: str, *, gallery: tuple[str, ...] = (), indexing: tuple[str, ...] = ()
) -> BearerGuard:
    app = Starlette(
        routes=[
            Route("/mcp", _ok),
            Route("/shutdown", _ok, methods=["POST"]),
            Route("/gallery/{token}/html", _ok),
            Route("/gallery/{token}/results", _ok),
            Route("/gallery/{token}/media/{kind}/{library}/{rest:path}", _ok),
            Route("/indexing/{session_id}/status", _ok),
        ]
    )
    return BearerGuard(
        app,
        token=token,
        gallery_sessions=_FakeManager(gallery),
        indexing_sessions=_FakeManager(indexing),
    )


@pytest.mark.parametrize(
    "path",
    ["/gallery/g1/html", "/gallery/g1/results", "/gallery/g1/media/thumbnail/lib/2024-07/hash"],
)
def test_bearer_guard_accepts_live_gallery_session_token_in_path(path: str) -> None:
    client = TestClient(_session_app("master", gallery=("g1",)))
    assert client.get(path).status_code == 200


def test_bearer_guard_rejects_unknown_gallery_session_token() -> None:
    client = TestClient(_session_app("master", gallery=("g1",)))
    assert client.get("/gallery/nope/html").status_code == 401
    assert client.get("/gallery/nope/media/thumbnail/lib/2024-07/hash").status_code == 401


def test_bearer_guard_rejects_session_token_on_control_plane() -> None:
    client = TestClient(_session_app("master", gallery=("g1",)))
    # A live gallery token is no credential for /mcp or /shutdown.
    assert client.get("/mcp").status_code == 401
    assert client.post("/shutdown").status_code == 401


def test_bearer_guard_accepts_live_indexing_session_token() -> None:
    client = TestClient(_session_app("master", indexing=("i1",)))
    assert client.get("/indexing/i1/status").status_code == 200


def test_bearer_guard_isolates_managers() -> None:
    # A gallery token cannot reach an indexing route and vice versa.
    client = TestClient(_session_app("master", gallery=("g1",), indexing=("i1",)))
    assert client.get("/indexing/g1/status").status_code == 401
    assert client.get("/gallery/i1/results").status_code == 401


def test_bearer_guard_master_token_reaches_control_plane_and_sessions() -> None:
    client = TestClient(_session_app("master"))
    hdr = {"Authorization": "Bearer master"}
    assert client.get("/mcp", headers=hdr).status_code == 200
    assert client.get("/gallery/anything/html", headers=hdr).status_code == 200


def test_host_origin_guard_accepts_allowed_host() -> None:
    client = TestClient(_host_app({"testserver"}))
    resp = client.get("/thing")
    assert resp.status_code == 200


def test_host_origin_guard_rejects_disallowed_host() -> None:
    client = TestClient(_host_app({"127.0.0.1:9999"}))
    resp = client.get("/thing")
    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("port", "expected"),
    [(1234, {"127.0.0.1:1234", "localhost:1234"})],
)
def test_allowed_hosts_for_port(port: int, expected: set[str]) -> None:
    assert allowed_hosts_for_port(port) == expected
