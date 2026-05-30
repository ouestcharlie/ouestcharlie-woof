"""Unit tests for GallerySessionManager."""

from __future__ import annotations

from woof.gallery_session_manager import _MAX_SESSIONS, GallerySessionManager

_DEFAULT_PAGE_SIZE = 496


def _manager_with_sessions(*sessions: dict) -> tuple[GallerySessionManager, list[str]]:
    """Create a manager pre-loaded with *sessions*, return (manager, tokens)."""
    mgr = GallerySessionManager()
    tokens = []
    for s in sessions:
        token = mgr.create(
            library_name=s.get("library", "lib"),
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
    token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE)
    assert isinstance(token, str) and token


def test_create_stores_session() -> None:
    mgr = GallerySessionManager()
    matches = [_match("h1")]
    token = mgr.create("mylib", {}, _DEFAULT_PAGE_SIZE, matches=matches)
    session = mgr.sessions[token]
    assert session.matches[0]["library"] == "mylib"


def test_create_tokens_are_unique() -> None:
    mgr = GallerySessionManager()
    tokens = {mgr.create("lib", {}, _DEFAULT_PAGE_SIZE) for _ in range(20)}
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
    mgr.create("lib", {}, _DEFAULT_PAGE_SIZE)
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
    tokens = [mgr.create("lib", {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]
    assert oldest in mgr.sessions

    mgr.create("lib", {}, _DEFAULT_PAGE_SIZE)  # triggers eviction

    assert oldest not in mgr.sessions
    assert len(mgr.sessions) == _MAX_SESSIONS


def test_create_keeps_newest_sessions_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create("lib", {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
    new_token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE)

    assert new_token in mgr.sessions
    # all but the first original token should still be present
    for tok in tokens[1:]:
        assert tok in mgr.sessions


def test_merge_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create("lib", {}, _DEFAULT_PAGE_SIZE) for _ in range(_MAX_SESSIONS)]
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
    token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE, matches=matches)
    hashes = [m["contentHash"] for m in mgr.sessions[token].matches]
    assert hashes == ["h3", "h1", "h2"]


def test_create_undated_photos_preserve_arrival_order() -> None:
    # No special handling for undated photos — arrival order is preserved as-is.
    matches = [
        _match("undated"),
        _match("dated", date_taken="2024-01-01T00:00:00"),
    ]
    mgr = GallerySessionManager()
    token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE, matches=matches)
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
# replace_page
# ---------------------------------------------------------------------------


def test_replace_page_updates_matches_and_page() -> None:
    mgr = GallerySessionManager()
    token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE, matches=[_match("h1")], page=0)
    session = mgr.sessions[token]
    session.replace_page([_match("h500")], page=1)
    assert session.page == 1
    assert session.matches[0]["contentHash"] == "h500"


def test_replace_page_stamps_library_on_new_matches() -> None:
    mgr = GallerySessionManager()
    token = mgr.create("mylib", {}, page=0, page_size=500, matches=[])
    session = mgr.sessions[token]
    session.replace_page([_match("h1")], page=1)
    assert session.matches[0]["library"] == "mylib"


# ---------------------------------------------------------------------------
# merge queryContext inheritance
# ---------------------------------------------------------------------------


def test_merge_single_session_inherits_query_context() -> None:
    mgr = GallerySessionManager()
    tok = mgr.create("lib", {}, matches=[_match("h1")], total_count=600, page_size=500, page=0)
    _, data = mgr.merge([tok])
    assert data.totalCount == 600
    assert data.pageSize == 500


def test_merge_multi_session_drops_query_context() -> None:
    mgr = GallerySessionManager()
    tok_a = mgr.create("lib_a", {}, page_size=500, page=0, matches=[_match("h1")])
    tok_b = mgr.create("lib_b", {}, page_size=500, page=0, matches=[_match("h2")])
    _, data = mgr.merge([tok_a, tok_b])
    assert data.libraryName != "lib_a"


# ---------------------------------------------------------------------------
# merge — large-total chaining
# ---------------------------------------------------------------------------


def _large_session(mgr: GallerySessionManager, library: str, count: int) -> str:
    """Create a session with *count* unique matches (enough to trigger chaining)."""
    matches = [_match(f"{library}-h{i}") for i in range(count)]
    return mgr.create(library, {}, page_size=_DEFAULT_PAGE_SIZE, matches=matches)


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
    assert data.type == "set"
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
    assert data.type == "set"
    assert data.chainedSessions == [mgr.sessions[tok_a], mgr.sessions[tok_b]]


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_unknown_token_returns_none() -> None:
    mgr = GallerySessionManager()
    session, page = mgr.get("no-such-token")
    assert session is None
    assert page == 0


def test_get_single_session_default_page() -> None:
    mgr = GallerySessionManager()
    token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE, matches=[_match("h1")])
    session, page = mgr.get(token)
    assert session is not None
    assert page == 0


def test_get_single_session_explicit_page_in_range() -> None:
    # totalCount larger than one page → page 1 is valid
    mgr = GallerySessionManager()
    token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE, total_count=_DEFAULT_PAGE_SIZE * 2)
    session, page = mgr.get(token, page=1)
    assert session is not None
    assert page == 1


def test_get_single_session_page_out_of_range_returns_none() -> None:
    # One match → totalCount/pageSize < 1 → only page 0 is valid
    mgr = GallerySessionManager()
    token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE, matches=[_match("h1")])
    session, _ = mgr.get(token, page=1)
    assert session is None


def test_get_single_session_last_valid_page() -> None:
    # 2 full pages: pages 0 and 1 are valid, page 2 is not
    mgr = GallerySessionManager()
    token = mgr.create("lib", {}, _DEFAULT_PAGE_SIZE, total_count=_DEFAULT_PAGE_SIZE * 2)
    session_p1, _ = mgr.get(token, page=1)
    session_p2, _ = mgr.get(token, page=2)
    assert session_p1 is not None
    assert session_p2 is None


def test_get_set_session_page_0_returns_first_chunk() -> None:
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "libA", _DEFAULT_PAGE_SIZE)
    tok_b = _large_session(mgr, "libB", _DEFAULT_PAGE_SIZE)
    merged_token, _ = mgr.merge([tok_a, tok_b])

    session, local_page = mgr.get(merged_token, page=0)
    assert session is not None
    assert session.libraryName == "libA"
    assert local_page == 0


def test_get_set_session_page_1_returns_second_chunk() -> None:
    # Each chunk has exactly 1 page → absolute page 1 maps to libB local page 0
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "libA", _DEFAULT_PAGE_SIZE)
    tok_b = _large_session(mgr, "libB", _DEFAULT_PAGE_SIZE)
    merged_token, _ = mgr.merge([tok_a, tok_b])

    session, local_page = mgr.get(merged_token, page=1)
    assert session is not None
    assert session.libraryName == "libB"
    assert local_page == 0


def test_get_set_session_out_of_range_returns_none() -> None:
    # 2 chunks × 1 page each → pages 0 and 1 valid, page 2 out of range
    mgr = GallerySessionManager()
    tok_a = _large_session(mgr, "libA", _DEFAULT_PAGE_SIZE)
    tok_b = _large_session(mgr, "libB", _DEFAULT_PAGE_SIZE)
    merged_token, _ = mgr.merge([tok_a, tok_b])

    session, _ = mgr.get(merged_token, page=2)
    assert session is None


def test_get_set_session_multi_page_first_chunk() -> None:
    # libA has totalCount = 2 * pageSize → 2 pages (0 and 1)
    # libB has 1 page → absolute page 2 maps to libB local page 0
    mgr = GallerySessionManager()
    matches_a = [_match(f"a{i}") for i in range(_DEFAULT_PAGE_SIZE)]
    tok_a = mgr.create(
        "libA",
        {},
        _DEFAULT_PAGE_SIZE,
        total_count=_DEFAULT_PAGE_SIZE * 2,
        matches=matches_a,
    )
    tok_b = _large_session(mgr, "libB", _DEFAULT_PAGE_SIZE)
    merged_token, _ = mgr.merge([tok_a, tok_b])

    session_p1, local_p1 = mgr.get(merged_token, page=1)
    assert session_p1 is not None
    assert session_p1.libraryName == "libA"
    assert local_p1 == 1

    session_p2, local_p2 = mgr.get(merged_token, page=2)
    assert session_p2 is not None
    assert session_p2.libraryName == "libB"
    assert local_p2 == 0
