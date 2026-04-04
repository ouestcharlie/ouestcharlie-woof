"""Unit tests for GallerySessionManager."""

from __future__ import annotations

from woof.gallery_session_manager import _MAX_SESSIONS, GallerySessionManager


def _manager_with_sessions(*sessions: dict) -> tuple[GallerySessionManager, list[str]]:
    """Create a manager pre-loaded with *sessions*, return (manager, tokens)."""
    mgr = GallerySessionManager()
    tokens = []
    for s in sessions:
        token = mgr.create(
            backend_name=s.get("backend", "lib"),
            matches=s.get("matches", []),
            http_port=s.get("http_port", 9999),
        )
        tokens.append(token)
    return mgr, tokens


def _match(content_hash: str, partition: str = "2024/01") -> dict:
    return {
        "contentHash": content_hash,
        "partition": partition,
        "filename": f"{content_hash}.jpg",
        "filePath": f"{partition}/{content_hash}.jpg",
    }


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_returns_token() -> None:
    mgr = GallerySessionManager()
    token = mgr.create("lib", [], 9999)
    assert isinstance(token, str) and token


def test_create_stores_session() -> None:
    mgr = GallerySessionManager()
    matches = [_match("h1")]
    token = mgr.create("mylib", matches, 8080)
    session = mgr.sessions[token]
    assert session["backend"] == "mylib"
    assert session["matches"] == matches
    assert session["httpPort"] == 8080
    assert session["querySummary"] == ""


def test_create_tokens_are_unique() -> None:
    mgr = GallerySessionManager()
    tokens = {mgr.create("lib", [], 9999) for _ in range(20)}
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
    mgr.create("lib", [], 9999)
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
    mgr, [tok] = _manager_with_sessions({"backend": "lib", "matches": matches})
    merged_token, data = mgr.merge([tok], "My query", 9999)
    assert data["matches"] == matches
    assert data["backend"] == "lib"
    assert data["querySummary"] == "My query"
    assert data["httpPort"] == 9999


def test_merge_creates_new_session() -> None:
    mgr, [tok] = _manager_with_sessions({"matches": [_match("h1")]})
    merged_token, _ = mgr.merge([tok], "", 9999)
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
    _, data = mgr.merge([tok_a, tok_b], "", 9999)
    hashes = [m["contentHash"] for m in data["matches"]]
    assert hashes == ["h1", "h2", "h3"]


def test_merge_preserves_first_seen_order() -> None:
    matches_a = [_match(f"h{i}") for i in range(3)]
    matches_b = [_match(f"h{i}") for i in range(2, 5)]  # h2 shared, h3/h4 new
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"matches": matches_a},
        {"matches": matches_b},
    )
    _, data = mgr.merge([tok_a, tok_b], "", 9999)
    hashes = [m["contentHash"] for m in data["matches"]]
    assert hashes == ["h0", "h1", "h2", "h3", "h4"]


def test_merge_joins_backend_names() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"backend": "lib1", "matches": []},
        {"backend": "lib2", "matches": []},
    )
    _, data = mgr.merge([tok_a, tok_b], "", 9999)
    assert data["backend"] == "lib1, lib2"


def test_merge_deduplicates_backend_names() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"backend": "lib1", "matches": []},
        {"backend": "lib1", "matches": []},
    )
    _, data = mgr.merge([tok_a, tok_b], "", 9999)
    assert data["backend"] == "lib1"


def test_merge_empty_sessions() -> None:
    mgr, [tok_a, tok_b] = _manager_with_sessions(
        {"backend": "", "matches": []},
        {"backend": "", "matches": []},
    )
    _, data = mgr.merge([tok_a, tok_b], "", 9999)
    assert data["matches"] == []
    assert data["backend"] == ""


# ---------------------------------------------------------------------------
# eviction (cap = _MAX_SESSIONS)
# ---------------------------------------------------------------------------


def test_create_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create("lib", [], 9999) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]
    assert oldest in mgr.sessions

    mgr.create("lib", [], 9999)  # triggers eviction

    assert oldest not in mgr.sessions
    assert len(mgr.sessions) == _MAX_SESSIONS


def test_create_keeps_newest_sessions_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create("lib", [], 9999) for _ in range(_MAX_SESSIONS)]
    new_token = mgr.create("lib", [], 9999)

    assert new_token in mgr.sessions
    # all but the first original token should still be present
    for tok in tokens[1:]:
        assert tok in mgr.sessions


def test_merge_evicts_oldest_when_full() -> None:
    mgr = GallerySessionManager()
    tokens = [mgr.create("lib", [], 9999) for _ in range(_MAX_SESSIONS)]
    oldest = tokens[0]

    # merge two of the existing sessions to trigger eviction
    merged_token, _ = mgr.merge([tokens[-2], tokens[-1]], "q", 9999)

    assert oldest not in mgr.sessions
    assert merged_token in mgr.sessions
    assert len(mgr.sessions) == _MAX_SESSIONS
