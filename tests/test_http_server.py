"""Tests for the gallery/proxy HTTP server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from unittest.mock import AsyncMock, MagicMock

import pytest

from woof.config import LibraryConfig
from woof.gallery_session_manager import GallerySessionManager, SessionHandler
from woof.http_server import start_http_server

_DEFAULT_SERVER_PAGE = 513


def _mock_agent(matches: list | None = None) -> MagicMock:
    agent = MagicMock()
    agent.call_tool = AsyncMock(return_value={"matches": matches or []})
    return agent


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
    tok = mgr.create(LibraryConfig(name="lib", type="filesystem", path="/tmp"), {}, 500, 1)
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/gallery/{tok}"
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
    tok = mgr.create(
        LibraryConfig(name="testlib", type="filesystem", path="/tmp"), {}, 600, 1, matches=matches
    )
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/api/results/{tok}"
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
    """Requesting the already-loaded page returns cached session data"""
    mgr = GallerySessionManager()
    tok = mgr.create(LibraryConfig(name="lib", type="filesystem", path="/tmp"), None, {}, 600, 1)
    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/api/results/{tok}/page/0") as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["pageMap"][0]["totalCount"] == 1


def test_page_endpoint_unknown_token_returns_404() -> None:
    server_url = start_http_server()
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/api/results/nope/page/1")
    assert exc_info.value.code == 404


def test_page_endpoint_calls_fetch_fn_and_returns_updated_session() -> None:
    mgr = GallerySessionManager()
    tok = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"), _mock_agent(), {}, 500, 600
    )
    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/api/results/{tok}/page/1") as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["pageMap"][0]["pageSize"] == 500


def test_page_endpoint_chained_session_loads_page() -> None:
    """Page endpoint serves chained pages without calling fetch_page_fn."""

    mgr = GallerySessionManager()
    agent = _mock_agent()
    matches_a = [{"contentHash": f"a{i}", "library": "lib"} for i in range(_DEFAULT_SERVER_PAGE)]
    matches_b = [{"contentHash": f"b{i}", "library": "lib"} for i in range(_DEFAULT_SERVER_PAGE)]
    tok_a = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent,
        {},
        _DEFAULT_SERVER_PAGE,
        matches=matches_a,
    )
    tok_b = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent,
        {},
        _DEFAULT_SERVER_PAGE,
        matches=matches_b,
    )
    merged_token, _ = mgr.merge([tok_a, tok_b])

    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/api/results/{merged_token}/page/1") as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["matches"][0]["contentHash"] == "b0"


def test_page_endpoint_chained_out_of_range_returns_404() -> None:
    mgr = GallerySessionManager()
    matches = [{"contentHash": f"h{i}", "library": "lib"} for i in range(_DEFAULT_SERVER_PAGE)]
    agent = _mock_agent()
    tok_a = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent,
        {},
        _DEFAULT_SERVER_PAGE,
        matches=matches,
    )
    tok_b = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent,
        {},
        _DEFAULT_SERVER_PAGE,
        matches=matches,
    )
    merged_token, _ = mgr.merge([tok_a, tok_b])

    server_url = start_http_server(session_manager=mgr)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/api/results/{merged_token}/page/5")
    assert exc_info.value.code == 404


def test_results_set_session_returns_aggregate_total_count() -> None:
    """api_results for a 'set' session must expose aggregate totalCount, not sub-session's."""
    mgr = GallerySessionManager()
    matches_a = [{"contentHash": f"a{i}", "library": "lib"} for i in range(_DEFAULT_SERVER_PAGE)]
    matches_b = [{"contentHash": f"b{i}", "library": "lib"} for i in range(_DEFAULT_SERVER_PAGE)]
    tok_a = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        None,
        {},
        _DEFAULT_SERVER_PAGE,
        matches=matches_a,
    )
    tok_b = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        None,
        {},
        _DEFAULT_SERVER_PAGE,
        matches=matches_b,
    )
    merged_token, _ = mgr.merge([tok_a, tok_b])

    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/api/results/{merged_token}") as resp:
        data = json.loads(resp.read())
    total = sum(e["totalCount"] for e in data["pageMap"])
    assert total == _DEFAULT_SERVER_PAGE * 2
    assert "chainedSessions" not in data


def test_results_set_session_returns_first_page_matches() -> None:
    """api_results for a 'set' session serves the first sub-session's matches."""
    mgr = GallerySessionManager()
    matches_a = [{"contentHash": f"a{i}", "library": "lib"} for i in range(_DEFAULT_SERVER_PAGE)]
    matches_b = [{"contentHash": f"b{i}", "library": "lib"} for i in range(_DEFAULT_SERVER_PAGE)]
    tok_a = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        None,
        {},
        _DEFAULT_SERVER_PAGE,
        matches=matches_a,
    )
    tok_b = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        None,
        {},
        _DEFAULT_SERVER_PAGE,
        matches=matches_b,
    )
    merged_token, _ = mgr.merge([tok_a, tok_b])

    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/api/results/{merged_token}") as resp:
        data = json.loads(resp.read())
    assert data["matches"][0]["contentHash"] == "a0"
    assert len(data["matches"]) == _DEFAULT_SERVER_PAGE


def test_results_single_session_exposes_pagination_fields() -> None:
    """api_results for a single session with totalCount > pageSize exposes pagination fields
    so the frontend can drive server-page navigation."""
    mgr = GallerySessionManager()
    tok = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        None,
        {},
        500,
        total_count=1200,
        matches=[],
    )
    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/api/results/{tok}") as resp:
        data = json.loads(resp.read())
    assert data["pageMap"] == [{"pageSize": 500, "pageCount": 3, "totalCount": 1200}]


def test_page_endpoint_passes_session_object_to_fetch_fn() -> None:
    """fetch_page_fn must receive (session: SessionData, page: int)."""

    mgr = GallerySessionManager()
    tok = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"), _mock_agent(), {}, 500, 600
    )
    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/api/results/{tok}/page/1") as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["pageMap"] == [{"pageSize": 500, "pageCount": 2, "totalCount": 600}]


def test_indexing_endpoint_returns_session() -> None:
    from woof.indexing_session_manager import IndexingSessionManager

    imgr = IndexingSessionManager()
    sid = imgr.start("lib", "")
    server_url = start_http_server(indexing_session_manager=imgr)
    with urllib.request.urlopen(f"{server_url}/api/indexing/{sid}") as resp:
        data = json.load(resp)
    assert data["status"] == "running"
    assert data["library_name"] == "lib"


def test_indexing_endpoint_unknown_returns_404() -> None:
    from woof.indexing_session_manager import IndexingSessionManager

    imgr = IndexingSessionManager()
    server_url = start_http_server(indexing_session_manager=imgr)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/api/indexing/nope")
    assert exc_info.value.code == 404


def test_cors_header_present_on_responses() -> None:
    """Responses to cross-origin requests must carry Access-Control-Allow-Origin: *.

    CORSMiddleware only adds the header when the request includes an Origin header,
    matching real browser behaviour.
    """
    mgr = GallerySessionManager()
    mgr.sessions["tok789"] = SessionHandler(
        library=LibraryConfig(name="lib", type="filesystem", path="/tmp"),
        agent=None,
        queryArgs={},
        pageSize=100,
        totalCount=1,
    )
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/api/results/tok789"
    req = urllib.request.Request(url, headers={"Origin": "http://example.com"})
    with urllib.request.urlopen(req) as resp:
        assert resp.headers["Access-Control-Allow-Origin"] == "*"
