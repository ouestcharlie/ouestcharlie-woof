"""Unit tests for GallerySessionManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from woof.agent_client import AgentError
from woof.config import LibraryConfig
from woof.gallery_session_manager import _MAX_SESSIONS, ChainedSessionHandler, GallerySessionManager

_DEFAULT_PAGE_SIZE = 496


def _lib(name: str = "lib") -> LibraryConfig:
    return LibraryConfig(name=name, type="filesystem", path="/tmp")


def _manager_with_sessions(*sessions: dict) -> tuple[GallerySessionManager, list[str]]:
    """Create a manager pre-loaded with *sessions*, return (manager, tokens)."""
    mgr = GallerySessionManager()
    tokens = []
    for s in sessions:
        token = mgr.create(
            library=_lib(s.get("library", "lib")),
            agent=None,
            query_args={},
            page_size=_DEFAULT_PAGE_SIZE,
            matches=s.get("matches", []),
        )
        tokens.append(token)
    return mgr, tokens


def _match(content_hash: str, partition: str = "2024/01", date_taken: str | None = None) -> dict:
    m: dict = {
        "contentHash": content_hash,
        "partition": partition,
        "filename": f"{content_hash}.jpg",
    }
    if date_taken is not None:
        m["dateTaken"] = date_taken
    return m


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_returns_token() -> None:
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE)
    assert isinstance(token, str) and token


def test_create_stores_session() -> None:
    mgr = GallerySessionManager()
    matches = [_match("h1")]
    token = mgr.create(_lib("mylib"), None, {}, _DEFAULT_PAGE_SIZE, matches=matches)
    session = mgr.sessions[token]
    assert session.matches[0]["library"] == "mylib"


def test_create_tokens_are_unique() -> None:
    mgr = GallerySessionManager()
    tokens = {mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE) for _ in range(20)}
    assert len(tokens) == 20


# ---------------------------------------------------------------------------
# sessions property
# ---------------------------------------------------------------------------


def test_sessions_is_shared_reference() -> None:
    """The same dict object must be returned each time."""
    mgr = GallerySessionManager()
    assert mgr.sessions is mgr.sessions


def test_sessions_reflects_creates() -> None:
    mgr = GallerySessionManager()
    assert len(mgr.sessions) == 0
    mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE)
    assert len(mgr.sessions) == 1


# ---------------------------------------------------------------------------
# unknown_tokens
# ---------------------------------------------------------------------------


def test_unknown_tokens_all_valid() -> None:
    mgr, tokens = _manager_with_sessions({}, {})
    assert mgr.unknown_tokens(tokens) == []


def test_unknown_tokens_detects_bad_token() -> None:
    mgr, tokens = _manager_with_sessions({})
    assert mgr.unknown_tokens([tokens[0], "bad"]) == ["bad"]


def test_unknown_tokens_empty_input() -> None:
    mgr = GallerySessionManager()
    assert mgr.unknown_tokens([]) == []


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def test_merge_single_session() -> None:
    matches = [_match("h1"), _match("h2")]
    mgr, [tok] = _manager_with_sessions({"library": "lib", "matches": matches})
    merged_token, data = mgr.merge([tok])
    hashes = [m["contentHash"] for m in data.matches]
    assert merged_token == tok
    assert hashes == ["h1", "h2"]


def test_merge_single_creates_no_new_session() -> None:
    mgr, [tok] = _manager_with_sessions({"matches": [_match("h1")]})
    merged_token, _ = mgr.merge([tok])
    assert merged_token in mgr.sessions
    assert merged_token == tok


def test_merge_deduplicates_by_content_hash() -> None:
    m1 = _match("h1")
    m2 = _match("h2")
    m3 = _match("h3")
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"matches": [m1, m2]},
        {"matches": [m2, m3]},  # m2 is a duplicate
    )
    _, data = mgr.merge([tok_a, tok_b])
    hashes = [m["contentHash"] for m in data.matches]
    assert hashes == ["h1", "h2", "h3"]


def test_merge_preserves_first_seen_order() -> None:
    matches_a = [_match(f"h{i}") for i in range(3)]
    matches_b = [_match(f"h{i}") for i in range(2, 5)]  # h2 shared, h3/h4 new
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"matches": matches_a},
        {"matches": matches_b},
    )
    _, data = mgr.merge([tok_a, tok_b])
    hashes = [m["contentHash"] for m in data.matches]
    assert hashes == ["h0", "h1", "h2", "h3", "h4"]


def test_merge_preserves_match_library_field() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"library": "lib1", "matches": [_match("h1")]},
        {"library": "lib2", "matches": [_match("h2")]},
    )
    _, data = mgr.merge([tok_a, tok_b])
    libs = [m["library"] for m in data.matches]
    assert libs == ["lib1", "lib2"]


def test_merge_empty_sessions() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"library": "lib1", "matches": []},
        {"library": "lib2", "matches": []},
    )
    _, data = mgr.merge([tok_a, tok_b])
    assert data.matches == []


# ---------------------------------------------------------------------------
# eviction (cap = _MAX_SESSIONS)
# ---------------------------------------------------------------------------


def test_create_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]
    assert oldest in mgr.sessions

    mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE)  # triggers eviction

    assert oldest not in mgr.sessions
    assert len(mgr.sessions) == _MAX_SESSIONS


def test_create_keeps_newest_sessions_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
    new_token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE)

    assert new_token in mgr.sessions
    # all but the first original token should still be present
    for tok in tokens[1:]:
        assert tok in mgr.sessions


def test_merge_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]

    # merge two of the existing sessions to trigger eviction
    merged_token, _ = mgr.merge([tokens[-2], tokens[-1]])

    assert oldest not in mgr.sessions
    assert merged_token in mgr.sessions
    assert len(mgr.sessions) == _MAX_SESSIONS


# ---------------------------------------------------------------------------
# date sorting
# ---------------------------------------------------------------------------


def test_create_preserves_arrival_order() -> None:
    # Sorting is done at the DB level
    # GallerySessionManager stores matches in the order they arrive without re-sorting.
    matches = [
        _match("h3", date_taken="2024-03-01T10:00:00"),
        _match("h1", date_taken="2024-01-01T10:00:00"),
        _match("h2", date_taken="2024-02-01T10:00:00"),
    ]
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE, matches=matches)
    hashes = [m["contentHash"] for m in mgr.sessions[token].matches]
    assert hashes == ["h3", "h1", "h2"]


def test_create_undated_photos_preserve_arrival_order() -> None:
    # No special handling for undated photos — arrival order is preserved as-is.
    matches = [
        _match("undated"),
        _match("dated", date_taken="2024-01-01T00:00:00"),
    ]
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE, matches=matches)
    hashes = [m["contentHash"] for m in mgr.sessions[token].matches]
    assert hashes == ["undated", "dated"]


def test_merge_preserves_per_library_arrival_order() -> None:
    # merge() concatenates per-library matches in token order, preserving
    # each library's internal arrival order (which reflects the DB sort).
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"matches": [_match("h3", date_taken="2024-03-01T00:00:00")]},
        {
            "matches": [
                _match("h1", date_taken="2024-01-01T00:00:00"),
                _match("h2", date_taken="2024-02-01T00:00:00"),
            ]
        },
    )
    _, data = mgr.merge([tok_a, tok_b])
    hashes = [m["contentHash"] for m in data.matches]
    assert hashes == ["h3", "h1", "h2"]


def test_merge_undated_photos_preserve_arrival_order() -> None:
    mgr, [tok] = _manager_with_sessions(
        {
            "matches": [
                _match("undated"),
                _match("dated", date_taken="2024-06-01T00:00:00"),
            ]
        }
    )
    _, data = mgr.merge([tok])
    hashes = [m["contentHash"] for m in data.matches]
    assert hashes == ["undated", "dated"]


# ---------------------------------------------------------------------------
# fetch_page
# ---------------------------------------------------------------------------


def _mock_agent(matches: list | None = None) -> MagicMock:
    agent = MagicMock()
    agent.call_tool = AsyncMock(return_value={"matches": matches or []})
    return agent


@pytest.mark.asyncio
async def test_fetch_page_returns_true_on_success() -> None:
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), _mock_agent(), {}, _DEFAULT_PAGE_SIZE, 2 * _DEFAULT_PAGE_SIZE + 1)
    session = mgr.sessions[token]
    assert await session.fetch_page(page=1) is True


@pytest.mark.asyncio
async def test_fetch_page_updates_matches_and_page() -> None:
    mgr = GallerySessionManager()
    agent = _mock_agent(matches=[_match("h1"), _match("h2")])
    token = mgr.create(_lib(), agent, {}, _DEFAULT_PAGE_SIZE, matches=[_match("h0")])
    session = mgr.sessions[token]
    await session.fetch_page(page=1)
    assert session.page == 1
    assert [m["contentHash"] for m in session.matches] == ["h1", "h2"]


@pytest.mark.asyncio
async def test_fetch_page_passes_query_args_and_page_to_agent() -> None:
    mgr = GallerySessionManager()
    agent = _mock_agent()
    token = mgr.create(
        _lib(), agent, {"sort_by": "date_taken"}, _DEFAULT_PAGE_SIZE, 4 * _DEFAULT_PAGE_SIZE
    )
    session = mgr.sessions[token]
    await session.fetch_page(page=3)
    agent.call_tool.assert_called_once_with(
        "wally", "search_photos", {"sort_by": "date_taken", "page": 3}, session.library
    )


@pytest.mark.asyncio
async def test_merge_simple_fetch_page_returns_true() -> None:
    mgr = GallerySessionManager()
    tok_a = mgr.create(_lib("a"), _mock_agent(), {}, _DEFAULT_PAGE_SIZE, 1, matches=[_match("h1")])
    tok_b = mgr.create(_lib("b"), _mock_agent(), {}, _DEFAULT_PAGE_SIZE, 1, matches=[_match("h2")])
    _, merged = mgr.merge([tok_a, tok_b])
    assert merged.library is None
    assert await merged.fetch_page(page=0) is True


@pytest.mark.asyncio
async def test_fetch_page_returns_false_on_agent_error() -> None:
    mgr = GallerySessionManager()
    agent = MagicMock()
    agent.call_tool = AsyncMock(side_effect=AgentError("wally down"))
    token = mgr.create(_lib(), agent, {}, _DEFAULT_PAGE_SIZE)
    session = mgr.sessions[token]
    assert await session.fetch_page(page=1) is False


# ---------------------------------------------------------------------------
# merge queryContext inheritance
# ---------------------------------------------------------------------------


def test_merge_single_session_inherits_query_context() -> None:
    mgr = GallerySessionManager()
    tok = mgr.create(
        _lib(), None, {}, matches=[_match("h1")], total_count=600, page_size=500, page=0
    )
    _, data = mgr.merge([tok])
    assert data.totalCount == 600
    assert data.pageSize == 500


def test_merge_multi_session_drops_query_context() -> None:
    mgr = GallerySessionManager()
    tok_a = mgr.create(_lib("lib_a"), None, {}, page_size=500, page=0, matches=[_match("h1")])
    tok_b = mgr.create(_lib("lib_b"), None, {}, page_size=500, page=0, matches=[_match("h2")])
    _, data = mgr.merge([tok_a, tok_b])
    assert data.library is None


# ---------------------------------------------------------------------------
# merge — large-total chaining
# ---------------------------------------------------------------------------


def _large_session(mgr: GallerySessionManager, library: str, count: int) -> str:
    """Create a session with *count* unique matches (enough to trigger chaining)."""
    matches = [_match(f"{library}-h{i}") for i in range(count)]
    return mgr.create(_lib(library), None, {}, page_size=_DEFAULT_PAGE_SIZE, matches=matches)


def test_merge_small_multi_session_stays_flat() -> None:
    """Total loaded matches below PAGE_SIZE → flat merge, no queryContext."""
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", _DEFAULT_PAGE_SIZE // 4)
    tok_b = _large_session(mgr, "b", _DEFAULT_PAGE_SIZE // 4)
    _, data = mgr.merge([tok_a, tok_b])
    assert data.pageSize == _DEFAULT_PAGE_SIZE
    assert len(data.matches) == _DEFAULT_PAGE_SIZE // 2


def test_merge_large_total_chains_sessions() -> None:
    """Total loaded matches > PAGE_SIZE → chained queryContext, first page loaded."""
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", _DEFAULT_PAGE_SIZE - 10)
    tok_b = _large_session(mgr, "b", _DEFAULT_PAGE_SIZE - 20)
    _, data = mgr.merge([tok_a, tok_b])
    assert isinstance(data, ChainedSessionHandler)
    assert data.chainedSessions == [mgr.sessions[tok_a], mgr.sessions[tok_b]]
    assert data.page == 0
    assert data.pageSize == _DEFAULT_PAGE_SIZE
    # Page 0 holds the first source session's matches
    assert len(data.matches) == _DEFAULT_PAGE_SIZE - 10
    assert data.matches[0]["contentHash"] == "a-h0"


def test_merge_chained_totalcount_sums_loaded_matches() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", 300)
    tok_b = _large_session(mgr, "b", 400)
    _, data = mgr.merge([tok_a, tok_b])
    assert data.totalCount == 700


def test_merge_at_page_boundary_chains() -> None:
    """PAGE_SIZE < total loaded matches → chained (not flat)."""
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", _DEFAULT_PAGE_SIZE // 2 + 1)
    tok_b = _large_session(mgr, "b", _DEFAULT_PAGE_SIZE // 2)
    _, data = mgr.merge([tok_a, tok_b])
    assert isinstance(data, ChainedSessionHandler)
    assert data.chainedSessions == [mgr.sessions[tok_a], mgr.sessions[tok_b]]


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_unknown_token_returns_none() -> None:
    mgr = GallerySessionManager()
    session = mgr.get("no-such-token")
    assert session is None


def test_get_single_session_default_page() -> None:
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE, matches=[_match("h1")])
    session = mgr.get(token)
    assert session is not None
    assert session.page == 0


def test_get_set_session_init_returns_first_chunk() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "libA", _DEFAULT_PAGE_SIZE)
    tok_b = _large_session(mgr, "libB", _DEFAULT_PAGE_SIZE)
    _, session = mgr.merge([tok_a, tok_b])

    assert isinstance(session, ChainedSessionHandler)
    assert session is not None
    assert session.library is None  # Chained Session has no library
    assert session.page == 0
