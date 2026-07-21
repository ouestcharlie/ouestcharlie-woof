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
