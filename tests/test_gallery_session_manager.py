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


def _mock_agent(matches: list | None = None) -> MagicMock:
    agent = MagicMock()
    agent.call_tool = AsyncMock(return_value={"matches": matches or []})
    return agent


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


def test_create_stamps_library_name_on_matches() -> None:
    mgr = GallerySessionManager()
    raw = [{"contentHash": "h1", "partition": "2024/01"}]
    token = mgr.create(_lib("photos"), None, {}, _DEFAULT_PAGE_SIZE, matches=raw)
    assert mgr.sessions[token].matches[0]["library"] == "photos"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_unknown_token_returns_none() -> None:
    mgr = GallerySessionManager()
    assert mgr.get("no-such-token") is None


def test_get_returns_session() -> None:
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE, matches=[_match("h1")])
    session = mgr.get(token)
    assert session is not None
    assert session.page == 0


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


def test_merge_single_returns_same_token() -> None:
    mgr, [tok] = _manager_with_sessions({"matches": [_match("h1"), _match("h2")]})
    merged_token, data = mgr.merge([tok])
    assert merged_token == tok
    assert [m["contentHash"] for m in data.matches] == ["h1", "h2"]


def test_merge_single_creates_no_new_session() -> None:
    mgr, [tok] = _manager_with_sessions({"matches": [_match("h1")]})
    merged_token, _ = mgr.merge([tok])
    assert merged_token == tok
    assert merged_token in mgr.sessions


def test_merge_deduplicates_by_content_hash() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"matches": [_match("h1"), _match("h2")]},
        {"matches": [_match("h2"), _match("h3")]},
    )
    _, data = mgr.merge([tok_a, tok_b])
    assert [m["contentHash"] for m in data.matches] == ["h1", "h2", "h3"]


def test_merge_preserves_match_library_field() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"library": "lib1", "matches": [_match("h1")]},
        {"library": "lib2", "matches": [_match("h2")]},
    )
    _, data = mgr.merge([tok_a, tok_b])
    assert [m["library"] for m in data.matches] == ["lib1", "lib2"]


def test_merge_empty_sessions() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions({"matches": []}, {"matches": []})
    _, data = mgr.merge([tok_a, tok_b])
    assert data.matches == []


def test_merge_single_inherits_total_count_and_page_size() -> None:
    mgr = GallerySessionManager()
    tok = mgr.create(
        _lib(), None, {}, matches=[_match("h1")], total_count=600, page_size=500, page=0
    )
    _, data = mgr.merge([tok])
    assert data.totalCount == 600
    assert data.pageSize == 500


def test_merge_multi_library_is_none() -> None:
    mgr = GallerySessionManager()
    tok_a = mgr.create(_lib("lib_a"), None, {}, page_size=500, page=0, matches=[_match("h1")])
    tok_b = mgr.create(_lib("lib_b"), None, {}, page_size=500, page=0, matches=[_match("h2")])
    _, data = mgr.merge([tok_a, tok_b])
    assert data.library is None


# ---------------------------------------------------------------------------
# merge — large-total chaining → ChainedSessionHandler
# ---------------------------------------------------------------------------


def _large_session(mgr: GallerySessionManager, library: str, count: int, agent=None) -> str:
    matches = [_match(f"{library}-h{i}") for i in range(count)]
    return mgr.create(_lib(library), agent, {}, page_size=_DEFAULT_PAGE_SIZE, matches=matches)


def test_merge_small_total_stays_flat() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", _DEFAULT_PAGE_SIZE // 4)
    tok_b = _large_session(mgr, "b", _DEFAULT_PAGE_SIZE // 4)
    _, data = mgr.merge([tok_a, tok_b])
    assert not isinstance(data, ChainedSessionHandler)
    assert len(data.matches) == _DEFAULT_PAGE_SIZE // 2


def test_merge_large_total_produces_chained_handler() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", _DEFAULT_PAGE_SIZE - 10)
    tok_b = _large_session(mgr, "b", _DEFAULT_PAGE_SIZE - 20)
    _, data = mgr.merge([tok_a, tok_b])
    assert isinstance(data, ChainedSessionHandler)
    assert data.chainedSessions == [mgr.sessions[tok_a], mgr.sessions[tok_b]]
    assert data.page == 0
    assert len(data.matches) == _DEFAULT_PAGE_SIZE - 10
    assert data.matches[0]["contentHash"] == "a-h0"


def test_merge_chained_totalcount_sums_loaded_matches() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", 300)
    tok_b = _large_session(mgr, "b", 400)
    _, data = mgr.merge([tok_a, tok_b])
    assert data.totalCount == 700


def test_merge_at_page_boundary_chains() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", _DEFAULT_PAGE_SIZE // 2 + 1)
    tok_b = _large_session(mgr, "b", _DEFAULT_PAGE_SIZE // 2)
    _, data = mgr.merge([tok_a, tok_b])
    assert isinstance(data, ChainedSessionHandler)


# ---------------------------------------------------------------------------
# eviction (cap = _MAX_SESSIONS)
# ---------------------------------------------------------------------------


def test_create_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]
    mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE)
    assert oldest not in mgr.sessions
    assert len(mgr.sessions) == _MAX_SESSIONS


def test_create_keeps_newest_sessions_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
    new_token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE)
    assert new_token in mgr.sessions
    for tok in tokens[1:]:
        assert tok in mgr.sessions


def test_merge_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]
    merged_token, _ = mgr.merge([tokens[-2], tokens[-1]])
    assert oldest not in mgr.sessions
    assert merged_token in mgr.sessions
    assert len(mgr.sessions) == _MAX_SESSIONS


# ---------------------------------------------------------------------------
# SessionHandler.fetch_page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_page_returns_true_on_success() -> None:
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), _mock_agent(), {}, _DEFAULT_PAGE_SIZE, 2 * _DEFAULT_PAGE_SIZE + 1)
    assert await mgr.sessions[token].fetch_page(page=1) is True


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
async def test_fetch_page_stamps_library_on_new_matches() -> None:
    mgr = GallerySessionManager()
    raw = [{"contentHash": "h99", "partition": "2024/01"}]
    token = mgr.create(
        _lib("mylib"), _mock_agent(matches=raw), {}, _DEFAULT_PAGE_SIZE, 2 * _DEFAULT_PAGE_SIZE
    )
    session = mgr.sessions[token]
    await session.fetch_page(page=1)
    assert session.matches[0]["library"] == "mylib"


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
async def test_fetch_page_idempotent_for_current_page() -> None:
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE, matches=[_match("h0")])
    session = mgr.sessions[token]
    assert await session.fetch_page(page=0) is True
    assert session.page == 0
    assert session.matches[0]["contentHash"] == "h0"


@pytest.mark.asyncio
async def test_fetch_page_returns_false_when_no_agent() -> None:
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), None, {}, _DEFAULT_PAGE_SIZE, total_count=2 * _DEFAULT_PAGE_SIZE)
    assert await mgr.sessions[token].fetch_page(page=1) is False


@pytest.mark.asyncio
async def test_fetch_page_returns_false_on_agent_error() -> None:
    agent = MagicMock()
    agent.call_tool = AsyncMock(side_effect=AgentError("wally down"))
    mgr = GallerySessionManager()
    token = mgr.create(_lib(), agent, {}, _DEFAULT_PAGE_SIZE, total_count=2 * _DEFAULT_PAGE_SIZE)
    assert await mgr.sessions[token].fetch_page(page=1) is False


# ---------------------------------------------------------------------------
# ChainedSessionHandler.fetch_page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chained_fetch_page_0_returns_first_session_matches() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "libA", _DEFAULT_PAGE_SIZE, agent=_mock_agent())
    tok_b = _large_session(mgr, "libB", _DEFAULT_PAGE_SIZE, agent=_mock_agent())
    _, chained = mgr.merge([tok_a, tok_b])
    assert isinstance(chained, ChainedSessionHandler)
    ok = await chained.fetch_page(page=0)
    assert ok is True
    assert chained.page == 0
    assert chained.matches[0]["contentHash"] == "libA-h0"


@pytest.mark.asyncio
async def test_chained_fetch_page_1_returns_second_session_matches() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "libA", _DEFAULT_PAGE_SIZE, agent=_mock_agent())
    tok_b = _large_session(mgr, "libB", _DEFAULT_PAGE_SIZE, agent=_mock_agent())
    _, chained = mgr.merge([tok_a, tok_b])
    ok = await chained.fetch_page(page=1)
    assert ok is True
    assert chained.page == 1
    assert chained.matches[0]["contentHash"] == "libB-h0"


@pytest.mark.asyncio
async def test_chained_fetch_page_out_of_range_returns_false() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "a", _DEFAULT_PAGE_SIZE)
    tok_b = _large_session(mgr, "b", _DEFAULT_PAGE_SIZE)
    _, chained = mgr.merge([tok_a, tok_b])
    assert await chained.fetch_page(page=99) is False


@pytest.mark.asyncio
async def test_chained_fetch_page_delegates_to_sub_session() -> None:
    """fetch_page on a chained session calls fetch_page on the correct sub-session.

    Session A has 1 wally page.  Session B has totalCount = 2*pageSize (2 wally pages)
    with only page 0 pre-loaded.  Chained absolute pages:
      0 → A page 0  (already loaded)
      1 → B page 0  (already loaded)
      2 → B page 1  (must be fetched — triggers agent_b.call_tool)
    """
    mgr = GallerySessionManager()
    agent_a = _mock_agent()
    agent_b = _mock_agent(matches=[_match("new-b1")])
    tok_a = _large_session(mgr, "libA", _DEFAULT_PAGE_SIZE, agent=agent_a)
    # Session B: 2 wally pages total, but only page 0 is loaded
    tok_b = mgr.create(
        _lib("libB"),
        agent_b,
        {},
        _DEFAULT_PAGE_SIZE,
        total_count=2 * _DEFAULT_PAGE_SIZE,
        matches=[_match(f"libB-h{i}") for i in range(_DEFAULT_PAGE_SIZE)],
    )
    _, chained = mgr.merge([tok_a, tok_b])
    # Page 2 = B's page 1 (not loaded yet) → must call agent_b
    await chained.fetch_page(page=2)
    agent_b.call_tool.assert_called_once()
    agent_a.call_tool.assert_not_called()
