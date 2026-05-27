"""Gallery session management for Woof.

Tracks search-result sessions keyed by URL-safe tokens.  The underlying
``sessions`` dict is shared with the HTTP server so it can serve
``/api/results/{token}`` without any additional coupling.
"""

from __future__ import annotations

import secrets
from typing import Any

_MAX_SESSIONS = 100


class GallerySessionManager:
    """Stores and retrieves gallery sessions keyed by URL-safe tokens.

    A *session* is a dict with at least::

        {
            "matches":      list[dict],   # photo match records; each match carries "library": str
            "querySummary": str,          # human-readable description
        }

    The ``sessions`` property exposes the underlying dict so the HTTP server
    can share it by reference without importing this class.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}  # insertion-ordered (Python 3.7+)

    def _add_session(self, token: str, data: dict[str, Any]) -> None:
        """Evict the oldest session if at capacity, then store *data* under *token*."""
        while len(self._sessions) >= _MAX_SESSIONS:
            del self._sessions[next(iter(self._sessions))]
        self._sessions[token] = data

    @property
    def sessions(self) -> dict[str, Any]:
        """Raw session dict — shared with the HTTP server."""
        return self._sessions

    def create(
        self,
        library_name: str,
        matches: list[Any],
        total_count: int | None = None,
    ) -> str:
        """Store a new search-result session and return its token.

        Args:
            library_name: Library the matches belong to.
            matches: Photo match records from a Wally search result.
            total_count: Wally's reported total for the query (may exceed
                ``len(matches)`` when results are paginated or capped).
                Defaults to ``len(matches)`` when omitted.
        """
        token = secrets.token_urlsafe(16)
        stamped = [{**m, "library": library_name} for m in matches]
        self._add_session(
            token,
            {
                "matches": stamped,
                "querySummary": "",
                "totalCount": total_count if total_count is not None else len(stamped),
            },
        )
        return token

    def get(self, token: str) -> dict[str, Any] | None:
        """Return the session for *token*, or ``None`` if not found."""
        return self._sessions.get(token)

    def unknown_tokens(self, tokens: list[str]) -> list[str]:
        """Return the subset of *tokens* not present in the session store."""
        return [t for t in tokens if t not in self._sessions]

    def merge(
        self,
        tokens: list[str],
        query_summary: str,
    ) -> tuple[str, dict[str, Any]]:
        """Merge sessions from *tokens* into a new session and return it.

        Matches are deduplicated by ``contentHash`` in first-seen order.
        Each match already carries its ``"library"`` field from :meth:`create`.

        Assumes all *tokens* are valid — call :meth:`unknown_tokens` first.

        Returns:
            ``(merged_token, session_data)`` where *session_data* has the
            standard session shape described on this class.
        """
        seen_hashes: set[str] = set()
        merged_matches: list[Any] = []

        for token in tokens:
            for match in self._sessions[token].get("matches", []):
                h = match.get("contentHash", "")
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    merged_matches.append(match)

        merged_token = secrets.token_urlsafe(16)
        session_data: dict[str, Any] = {
            "matches": merged_matches,
            "querySummary": query_summary,
            "totalCount": len(merged_matches),
        }
        self._add_session(merged_token, session_data)
        return merged_token, session_data
