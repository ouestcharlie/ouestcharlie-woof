"""Tests for the gallery/proxy HTTP server."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from woof.gallery_session_manager import GallerySessionManager
from woof.http_server import start_http_server


def test_thumbnail_without_wally_returns_503() -> None:
    """Thumbnail requests are proxied to Wally; without a Wally port → 503."""
    server_url = start_http_server()
    url = f"{server_url}/thumbnails/testlib/2024/2024-07/thumbnails.avif"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 503


def test_preview_without_wally_returns_503() -> None:
    """Preview requests are proxied to Wally; without a Wally port configured → 503."""
    server_url = start_http_server()
    url = f"{server_url}/previews/testlib/2024/2024-07/abc123.jpg"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 503


def test_gallery_token_route_serves_html() -> None:
    mgr = GallerySessionManager()
    mgr.sessions["tok123"] = {"matches": [], "querySummary": ""}
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/gallery/tok123"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        body = resp.read().decode()
        assert "<html" in body


def test_gallery_unknown_token_returns_404() -> None:
    server_url = start_http_server()
    url = f"{server_url}/gallery/nosuchtoken"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 404


def test_results_endpoint_returns_session_data() -> None:
    import json

    matches = [{"partition": "2024/2024-07", "filename": "a.jpg", "library": "testlib"}]
    mgr = GallerySessionManager()
    mgr.sessions["tok456"] = {"matches": matches, "querySummary": ""}
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/api/results/tok456"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["matches"][0]["library"] == "testlib"
        assert data["matches"] == matches


def test_results_unknown_token_returns_404() -> None:
    server_url = start_http_server()
    url = f"{server_url}/api/results/nope"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 404


def test_gallery_static_not_intercepted_by_proxy() -> None:
    """Requests to /gallery-static/ must reach StaticFiles, not proxy_media.

    A missing file returns 404 (StaticFiles); if the catch-all proxy_media
    intercepted it first, we would get 503 (no Wally configured).
    """
    server_url = start_http_server()
    url = f"{server_url}/gallery-static/nonexistent.js"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 404


def test_page_endpoint_idempotent_for_current_page() -> None:
    """Requesting the already-loaded page returns cached session data
    without calling fetch_page_fn."""
    import json

    called = []

    def fetch_fn(token: str, page: int) -> bool:
        called.append((token, page))
        return True

    mgr = GallerySessionManager()
    qc = {"library_name": "lib", "args": {}, "page": 0, "pageSize": 500}
    mgr.sessions["tok"] = {
        "matches": [],
        "querySummary": "",
        "totalCount": 1,
        "queryContext": qc,
    }
    server_url = start_http_server(session_manager=mgr, fetch_page_fn=fetch_fn)
    with urllib.request.urlopen(f"{server_url}/api/results/tok/page/0") as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["queryContext"]["page"] == 0
    assert called == []  # fetch_fn must NOT be invoked for the current page


def test_page_endpoint_unknown_token_returns_404() -> None:
    server_url = start_http_server()
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/api/results/nope/page/1")
    assert exc_info.value.code == 404


def test_page_endpoint_no_context_returns_400() -> None:
    mgr = GallerySessionManager()
    mgr.sessions["tok"] = {
        "matches": [],
        "querySummary": "",
        "totalCount": 0,
        "queryContext": None,
    }
    server_url = start_http_server(session_manager=mgr)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/api/results/tok/page/1")
    assert exc_info.value.code == 400


def test_page_endpoint_calls_fetch_fn_and_returns_updated_session() -> None:
    import json

    fetched: list = []

    def fetch_fn(token: str, page: int) -> bool:
        fetched.append((token, page))
        mgr.sessions[token]["queryContext"]["page"] = page
        return True

    mgr = GallerySessionManager()
    qc = {"library_name": "lib", "args": {}, "page": 0, "pageSize": 500}
    mgr.sessions["tok"] = {
        "matches": [],
        "querySummary": "",
        "totalCount": 600,
        "queryContext": qc,
    }
    server_url = start_http_server(session_manager=mgr, fetch_page_fn=fetch_fn)
    with urllib.request.urlopen(f"{server_url}/api/results/tok/page/1") as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["queryContext"]["page"] == 1
    assert fetched == [("tok", 1)]


def test_page_endpoint_returns_502_when_fetch_fn_fails() -> None:
    def fetch_fn(token: str, page: int) -> bool:
        return False

    mgr = GallerySessionManager()
    qc = {"library_name": "lib", "args": {}, "page": 0, "pageSize": 500}
    mgr.sessions["tok"] = {
        "matches": [],
        "querySummary": "",
        "totalCount": 600,
        "queryContext": qc,
    }
    server_url = start_http_server(session_manager=mgr, fetch_page_fn=fetch_fn)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/api/results/tok/page/1")
    assert exc_info.value.code == 502


def test_cors_header_present_on_responses() -> None:
    """Responses to cross-origin requests must carry Access-Control-Allow-Origin: *.

    CORSMiddleware only adds the header when the request includes an Origin header,
    matching real browser behaviour.
    """
    mgr = GallerySessionManager()
    mgr.sessions["tok789"] = {"matches": [], "querySummary": ""}
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/api/results/tok789"
    req = urllib.request.Request(url, headers={"Origin": "http://example.com"})
    with urllib.request.urlopen(req) as resp:
        assert resp.headers["Access-Control-Allow-Origin"] == "*"
