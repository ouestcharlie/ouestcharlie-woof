"""Tests for the gallery/proxy HTTP server."""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock, MagicMock

import pytest
from http_test_server import start_http_server

from woof.agent_client import AgentError
from woof.config import LibraryConfig
from woof.gallery_session_manager import GallerySessionManager, SessionHandler
from woof.http_server import get_gallery_html
from woof.indexing_session_manager import IndexingSessionManager

_DEFAULT_SERVER_PAGE = 513


def _mock_agent(matches: list | None = None) -> MagicMock:
    agent = MagicMock()
    agent.call_tool = AsyncMock(return_value={"matches": matches or []})
    return agent


def _session_with_match(match: dict, **manager_kwargs) -> tuple[GallerySessionManager, str]:
    """A GallerySessionManager holding one session whose single match is *match*."""
    mgr = GallerySessionManager()
    tok = mgr.create(
        LibraryConfig(name=match["library"], type="filesystem", path="/tmp"),
        None,
        {},
        500,
        matches=[match],
    )
    return mgr, tok


_MEDIA_MATCH = {
    "library": "testlib",
    "partition": "2024/2024-07",
    "avifHash": "grid1",
    "contentHash": "abc123",
    "tileIndex": 0,
}


def test_thumbnail_without_wally_returns_503() -> None:
    """Thumbnail requests are proxied to Wally; without a Wally port → 503."""
    mgr, tok = _session_with_match(_MEDIA_MATCH)
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/gallery/{tok}/media/thumbnail/testlib/2024/2024-07/grid1"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 503


def test_preview_without_wally_returns_503() -> None:
    """Preview requests are proxied to Wally; without a Wally port configured → 503."""
    mgr, tok = _session_with_match(_MEDIA_MATCH)
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/gallery/{tok}/media/previews/testlib/2024/2024-07/abc123.jpg"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 503


def test_media_unknown_session_returns_404() -> None:
    """A /media request whose token names no live session is rejected."""
    server_url = start_http_server()
    url = f"{server_url}/gallery/nosuchtoken/media/previews/testlib/2024/2024-07/abc123.jpg"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 404


def test_media_file_not_in_session_returns_403() -> None:
    """A valid session token cannot fetch a file outside its match set."""
    mgr, tok = _session_with_match(_MEDIA_MATCH)
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/gallery/{tok}/media/previews/testlib/2024/2024-07/otherhash.jpg"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 403


def test_gallery_token_route_serves_html() -> None:
    mgr = GallerySessionManager()
    tok = mgr.create(LibraryConfig(name="lib", type="filesystem", path="/tmp"), {}, 500, 1)
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/gallery/{tok}/html"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        body = resp.read().decode()
        assert "<html" in body


def test_get_gallery_html_rewrites_static_assets_to_absolute_urls() -> None:
    html = get_gallery_html("http://localhost:12345")
    # Vite's root-relative asset base is rewritten to an absolute server URL.
    assert "http://localhost:12345/gallery-static/" in html
    # No inline <script> should be introduced — CSP script-src should not need widening.
    assert "<script>window" not in html


def test_get_gallery_html_embeds_no_data_attributes() -> None:
    # Origins come from the tool result / location.origin, the session id from the
    # URL path / tool result — nothing session- or origin-specific is embedded.
    html = get_gallery_html("http://localhost:12345")
    assert "data-session-id" not in html
    assert "data-server-urls" not in html


def test_gallery_unknown_token_returns_404() -> None:
    server_url = start_http_server()
    url = f"{server_url}/gallery/nosuchtoken/html"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 404


def test_results_endpoint_returns_session_data() -> None:
    matches = [{"partition": "2024/2024-07", "filename": "a.jpg", "library": "testlib"}]
    mgr = GallerySessionManager()
    tok = mgr.create(
        LibraryConfig(name="testlib", type="filesystem", path="/tmp"), {}, 600, 1, matches=matches
    )
    server_url = start_http_server(session_manager=mgr)
    url = f"{server_url}/gallery/{tok}/results"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["matches"][0]["library"] == "testlib"
        assert data["matches"] == matches


def test_results_unknown_token_returns_404() -> None:
    server_url = start_http_server()
    url = f"{server_url}/gallery/nope/results"
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
    with urllib.request.urlopen(f"{server_url}/gallery/{tok}/results/page/0") as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["pageMap"][0]["totalCount"] == 1


def test_page_endpoint_unknown_token_returns_404() -> None:
    server_url = start_http_server()
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/gallery/nope/results/page/1")
    assert exc_info.value.code == 404


def test_page_endpoint_calls_fetch_fn_and_returns_updated_session() -> None:
    mgr = GallerySessionManager()
    tok = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"), _mock_agent(), {}, 500, 600
    )
    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/gallery/{tok}/results/page/1") as resp:
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
    with urllib.request.urlopen(f"{server_url}/gallery/{merged_token}/results/page/1") as resp:
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
        urllib.request.urlopen(f"{server_url}/gallery/{merged_token}/results/page/5")
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
    with urllib.request.urlopen(f"{server_url}/gallery/{merged_token}/results") as resp:
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
    with urllib.request.urlopen(f"{server_url}/gallery/{merged_token}/results") as resp:
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
    with urllib.request.urlopen(f"{server_url}/gallery/{tok}/results") as resp:
        data = json.loads(resp.read())
    assert data["pageMap"] == [{"pageSize": 500, "pageCount": 3, "totalCount": 1200}]


def test_page_endpoint_passes_session_object_to_fetch_fn() -> None:
    """fetch_page_fn must receive (session: SessionData, page: int)."""

    mgr = GallerySessionManager()
    tok = mgr.create(
        LibraryConfig(name="lib", type="filesystem", path="/tmp"), _mock_agent(), {}, 500, 600
    )
    server_url = start_http_server(session_manager=mgr)
    with urllib.request.urlopen(f"{server_url}/gallery/{tok}/results/page/1") as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["pageMap"] == [{"pageSize": 500, "pageCount": 2, "totalCount": 600}]


def test_indexing_endpoint_returns_session() -> None:
    imgr = IndexingSessionManager()
    sid = imgr.start("lib", "")
    server_url = start_http_server(indexing_session_manager=imgr)
    with urllib.request.urlopen(f"{server_url}/indexing/{sid}/status") as resp:
        data = json.load(resp)
    assert data["status"] == "running"
    assert data["library_name"] == "lib"


def test_indexing_endpoint_unknown_returns_404() -> None:
    imgr = IndexingSessionManager()
    server_url = start_http_server(indexing_session_manager=imgr)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/indexing/nope/status")
    assert exc_info.value.code == 404


def test_cancel_endpoint_returns_cancelling() -> None:
    imgr = IndexingSessionManager()
    sid = imgr.start("lib", "")
    imgr.register_task(sid, MagicMock(spec=asyncio.Task))
    server_url = start_http_server(indexing_session_manager=imgr)
    req = urllib.request.Request(f"{server_url}/indexing/{sid}/cancel", method="POST", data=b"")
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    assert data["status"] == "cancelling"
    assert imgr.get(sid)["status"] == "cancelling"


def test_cancel_endpoint_returns_409_when_not_running() -> None:
    imgr = IndexingSessionManager()
    sid = imgr.start("lib", "")
    imgr.complete(sid, {})
    server_url = start_http_server(indexing_session_manager=imgr)
    req = urllib.request.Request(f"{server_url}/indexing/{sid}/cancel", method="POST", data=b"")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 409


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
    url = f"{server_url}/gallery/tok789/results"
    req = urllib.request.Request(url, headers={"Origin": "http://example.com"})
    with urllib.request.urlopen(req) as resp:
        assert resp.headers["Access-Control-Allow-Origin"] == "*"


def test_indexing_html_route_serves_html() -> None:
    imgr = IndexingSessionManager()
    sid = imgr.start("MyLib", [])
    server_url = start_http_server(indexing_session_manager=imgr)
    with urllib.request.urlopen(f"{server_url}/indexing/{sid}/html") as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        assert "<html" in resp.read().decode()


def test_indexing_html_unknown_session_returns_404() -> None:
    imgr = IndexingSessionManager()
    server_url = start_http_server(indexing_session_manager=imgr)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/indexing/nope/html")
    assert exc_info.value.code == 404


def test_indexing_endpoint_returns_503_when_not_configured() -> None:
    server_url = start_http_server()
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/indexing/any-id/status")
    assert exc_info.value.code == 503


def test_cancel_endpoint_returns_503_when_not_configured() -> None:
    server_url = start_http_server()
    req = urllib.request.Request(f"{server_url}/indexing/any-id/cancel", method="POST", data=b"")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 503


def test_cancel_endpoint_returns_409_for_unknown_session() -> None:
    imgr = IndexingSessionManager()
    server_url = start_http_server(indexing_session_manager=imgr)
    req = urllib.request.Request(f"{server_url}/indexing/no-such/cancel", method="POST", data=b"")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 409


def test_page_endpoint_returns_400_for_invalid_page() -> None:
    mgr = GallerySessionManager()
    tok = mgr.create(LibraryConfig(name="lib", type="filesystem", path="/tmp"), None, {}, 500, 1)
    server_url = start_http_server(session_manager=mgr)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/gallery/{tok}/results/page/abc")
    assert exc_info.value.code == 400


def test_page_endpoint_returns_502_on_fetch_failure() -> None:
    agent = MagicMock()
    agent.call_tool = AsyncMock(side_effect=AgentError("agent down"))
    mgr = GallerySessionManager()
    tok = mgr.create(LibraryConfig(name="lib", type="filesystem", path="/tmp"), agent, {}, 500, 600)
    server_url = start_http_server(session_manager=mgr)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{server_url}/gallery/{tok}/results/page/1")
    assert exc_info.value.code == 502


# ---------------------------------------------------------------------------
# proxy_media — video Range forwarding + streaming passthrough (OEC-39a §2)
# ---------------------------------------------------------------------------

_UPSTREAM_BODY = bytes(range(256)) * 40  # 10240 deterministic bytes


class _FakeWallyHandler(BaseHTTPRequestHandler):
    """Stand-in for Wally: honours single-range requests and records headers."""

    received_headers: dict = {}

    def log_message(self, *args):  # silence test noise
        pass

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        type(self).received_headers = dict(self.headers)
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start_s, _, end_s = rng[len("bytes=") :].partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(_UPSTREAM_BODY) - 1
            slice_ = _UPSTREAM_BODY[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(slice_)))
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(_UPSTREAM_BODY)}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(slice_)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(_UPSTREAM_BODY)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(_UPSTREAM_BODY)


def _start_fake_wally() -> tuple[int, str]:
    """Start the fake upstream on 127.0.0.1 and return (port, token)."""
    _FakeWallyHandler.received_headers = {}
    server = HTTPServer(("127.0.0.1", 0), _FakeWallyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1], "sekret"


def test_proxy_video_range_passthrough() -> None:
    """A Range request is forwarded to Wally and its 206 flows back unchanged."""
    port, token = _start_fake_wally()
    mgr, tok = _session_with_match(_MEDIA_MATCH)
    server_url = start_http_server(
        session_manager=mgr, wally_connection_fn=lambda lib: (port, token)
    )
    url = f"{server_url}/gallery/{tok}/media/video/testlib/2024/2024-07/abc123.mp4"
    req = urllib.request.Request(url, headers={"Range": "bytes=100-199"})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 206
        assert resp.headers["Content-Range"] == f"bytes 100-199/{len(_UPSTREAM_BODY)}"
        assert resp.headers["Accept-Ranges"] == "bytes"
        assert resp.read() == _UPSTREAM_BODY[100:200]
    # The proxy forwarded the Range header and the bearer token upstream.
    assert _FakeWallyHandler.received_headers.get("Range") == "bytes=100-199"
    assert _FakeWallyHandler.received_headers.get("Authorization") == f"Bearer {token}"


def test_proxy_video_full_body_when_no_range() -> None:
    port, token = _start_fake_wally()
    mgr, tok = _session_with_match(_MEDIA_MATCH)
    server_url = start_http_server(
        session_manager=mgr, wally_connection_fn=lambda lib: (port, token)
    )
    url = f"{server_url}/gallery/{tok}/media/video/testlib/2024/2024-07/abc123.mp4"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "video/mp4"
        assert resp.read() == _UPSTREAM_BODY
    assert "Range" not in _FakeWallyHandler.received_headers
