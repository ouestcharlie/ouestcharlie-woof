"""Unit tests for GallerySessionManager."""

from __future__ import annotations

from woof.gallery_session_manager import _MAX_SESSIONS, GallerySessionManager


def _manager_with_sessions(*sessions: dict) -> tuple[GallerySessionManager, list[str]]:
    """Create a manager pre-loaded with *sessions*, return (manager, tokens)."""
    mgr = GallerySessionManager()
    tokens = []
    for s in sessions:
        token = mgr.create(
            library_name=s.get("library", "lib"),
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
    token = mgr.create("lib", [])
    assert isinstance(token, str) and token


def test_create_stores_session() -> None:
    mgr = GallerySessionManager()
    matches = [_match("h1")]
    token = mgr.create("mylib", matches)
    session = mgr.sessions[token]
    assert session["matches"][0]["library"] == "mylib"
    assert session["querySummary"] == ""


def test_create_tokens_are_unique() -> None:
    mgr = GallerySessionManager()
    tokens = {mgr.create("lib", []) for _ in range(20)}
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
    mgr.create("lib", [])
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
    merged_token, data = mgr.merge([tok], "My query")
    hashes = [m["contentHash"] for m in data["matches"]]
    assert hashes == ["h1", "h2"]
    assert data["querySummary"] == "My query"


def test_merge_creates_new_session() -> None:
    mgr, [tok] = _manager_with_sessions({"matches": [_match("h1")]})
    merged_token, _ = mgr.merge([tok], "")
    assert merged_token in mgr.sessions
    assert merged_token != tok


def test_merge_deduplicates_by_content_hash() -> None:
    m1 = _match("h1")
    m2 = _match("h2")
    m3 = _match("h3")
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"matches": [m1, m2]},
        {"matches": [m2, m3]},  # m2 is a duplicate
    )
    _, data = mgr.merge([tok_a, tok_b], "")
    hashes = [m["contentHash"] for m in data["matches"]]
    assert hashes == ["h1", "h2", "h3"]


def test_merge_preserves_first_seen_order() -> None:
    matches_a = [_match(f"h{i}") for i in range(3)]
    matches_b = [_match(f"h{i}") for i in range(2, 5)]  # h2 shared, h3/h4 new
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"matches": matches_a},
        {"matches": matches_b},
    )
    _, data = mgr.merge([tok_a, tok_b], "")
    hashes = [m["contentHash"] for m in data["matches"]]
    assert hashes == ["h0", "h1", "h2", "h3", "h4"]


def test_merge_preserves_match_library_field() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"library": "lib1", "matches": [_match("h1")]},
        {"library": "lib2", "matches": [_match("h2")]},
    )
    _, data = mgr.merge([tok_a, tok_b], "")
    libs = [m["library"] for m in data["matches"]]
    assert libs == ["lib1", "lib2"]


def test_merge_empty_sessions() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"library": "lib1", "matches": []},
        {"library": "lib2", "matches": []},
    )
    _, data = mgr.merge([tok_a, tok_b], "")
    assert data["matches"] == []


# ---------------------------------------------------------------------------
# eviction (cap = _MAX_SESSIONS)
# ---------------------------------------------------------------------------


def test_create_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create("lib", []) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]
    assert oldest in mgr.sessions

    mgr.create("lib", [])  # triggers eviction

    assert oldest not in mgr.sessions
    assert len(mgr.sessions) == _MAX_SESSIONS


def test_create_keeps_newest_sessions_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create("lib", []) for _ in range(_MAX_SESSIONS)]
    new_token = mgr.create("lib", [])

    assert new_token in mgr.sessions
    # all but the first original token should still be present
    for tok in tokens[1:]:
        assert tok in mgr.sessions


def test_merge_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create("lib", []) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]

    # merge two of the existing sessions to trigger eviction
    merged_token, _ = mgr.merge([tokens[-2], tokens[-1]], "q")

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
    token = mgr.create("lib", matches)
    hashes = [m["contentHash"] for m in mgr.sessions[token]["matches"]]
    assert hashes == ["h3", "h1", "h2"]


def test_create_undated_photos_preserve_arrival_order() -> None:
    # No special handling for undated photos — arrival order is preserved as-is.
    matches = [
        _match("undated"),
        _match("dated", date_taken="2024-01-01T00:00:00"),
    ]
    mgr = GallerySessionManager()
    token = mgr.create("lib", matches)
    hashes = [m["contentHash"] for m in mgr.sessions[token]["matches"]]
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
    _, data = mgr.merge([tok_a, tok_b], "")
    hashes = [m["contentHash"] for m in data["matches"]]
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
    _, data = mgr.merge([tok], "")
    hashes = [m["contentHash"] for m in data["matches"]]
    assert hashes == ["undated", "dated"]


# ---------------------------------------------------------------------------
# query_context on create
# ---------------------------------------------------------------------------


def test_create_stores_query_context() -> None:
    mgr = GallerySessionManager()
    qc = {"library_name": "lib", "args": {}, "page": 0, "pageSize": 500}
    token = mgr.create("lib", [], query_context=qc)
    assert mgr.sessions[token]["queryContext"] == qc


def test_create_without_query_context_is_none() -> None:
    mgr = GallerySessionManager()
    token = mgr.create("lib", [])
    assert mgr.sessions[token]["queryContext"] is None


# ---------------------------------------------------------------------------
# replace_page
# ---------------------------------------------------------------------------


def test_replace_page_updates_matches_and_page() -> None:
    mgr = GallerySessionManager()
    qc = {"library_name": "lib", "args": {}, "page": 0, "pageSize": 500}
    token = mgr.create("lib", [_match("h1")], query_context=qc)
    mgr.replace_page(token, [_match("h500")], page=1)
    session = mgr.sessions[token]
    assert session["queryContext"]["page"] == 1
    assert session["matches"][0]["contentHash"] == "h500"


def test_replace_page_stamps_library_on_new_matches() -> None:
    mgr = GallerySessionManager()
    qc = {"library_name": "mylib", "args": {}, "page": 0, "pageSize": 500}
    token = mgr.create("mylib", [], query_context=qc)
    mgr.replace_page(token, [_match("h1")], page=1)
    assert mgr.sessions[token]["matches"][0]["library"] == "mylib"


def test_replace_page_unknown_token_is_no_op() -> None:
    mgr = GallerySessionManager()
    mgr.replace_page("no-such-token", [], page=1)  # must not raise


def test_replace_page_no_query_context_is_no_op() -> None:
    mgr = GallerySessionManager()
    token = mgr.create("lib", [_match("h1")])  # no query_context → None
    mgr.replace_page(token, [_match("h2")], page=1)  # must not raise or change matches
    assert mgr.sessions[token]["matches"][0]["contentHash"] == "h1"


# ---------------------------------------------------------------------------
# merge queryContext inheritance
# ---------------------------------------------------------------------------


def test_merge_single_session_inherits_query_context() -> None:
    mgr = GallerySessionManager()
    qc = {"library_name": "lib", "args": {}, "page": 0, "pageSize": 500}
    tok = mgr.create("lib", [_match("h1")], total_count=600, query_context=qc)
    _, data = mgr.merge([tok], "q")
    assert data["queryContext"] == qc
    assert data["totalCount"] == 600


def test_merge_multi_session_drops_query_context() -> None:
    mgr = GallerySessionManager()
    qc = {"library_name": "lib", "args": {}, "page": 0, "pageSize": 500}
    tok_a = mgr.create("lib", [_match("h1")], query_context=qc)
    tok_b = mgr.create("lib", [_match("h2")], query_context=qc)
    _, data = mgr.merge([tok_a, tok_b], "q")
    assert data["queryContext"] is None


def test_merge_single_session_without_query_context_stays_none() -> None:
    mgr = GallerySessionManager()
    tok = mgr.create("lib", [_match("h1")])  # queryContext is None
    _, data = mgr.merge([tok], "q")
    assert data["queryContext"] is None
