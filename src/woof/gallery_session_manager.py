"""Gallery session management for Woof.

Tracks search-result sessions keyed by URL-safe tokens.  The underlying
``sessions`` dict is shared with the HTTP server so it can serve
``/api/results/{token}`` without any additional coupling.
"""

from __future__ import annotations

import secrets
from typing import Any

_MAX_SESSIONS = 100

# Sentinel that sorts after any ISO datetime string so undated photos go last.
_NO_DATE = "\uffff"


def _sort_by_date(matches: list[Any]) -> list[Any]:
    """Return *matches* sorted by ``dateTaken`` ascending; undated photos last."""
    return sorted(matches, key=lambda m: m.get("dateTaken") or _NO_DATE)


class GallerySessionManager:
    """Stores and retrieves gallery sessions keyed by URL-safe tokens.

    A *session* is a dict with at least::

        {
            "matches":      list[dict],   # photo match records
            "backend":      str,          # backend name(s)
            "httpPort":     int,          # Woof HTTP port
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

    def create(self, backend_name: str, matches: list[Any], http_port: int) -> str:
        """Store a new search-result session and return its token."""
        token = secrets.token_urlsafe(16)
        self._add_session(
            token,
            {
                "matches": _sort_by_date(matches),
                "backend": backend_name,
                "httpPort": http_port,
                "querySummary": "",
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
        http_port: int,
    ) -> tuple[str, dict[str, Any]]:
        """Merge sessions from *tokens* into a new session and return it.

        Matches are deduplicated by ``contentHash`` in first-seen order.
        Backend names are joined with ``", "`` when sessions span several backends.

        Assumes all *tokens* are valid — call :meth:`unknown_tokens` first.

        Returns:
            ``(merged_token, session_data)`` where *session_data* has the
            standard session shape described on this class.
        """
        seen_hashes: set[str] = set()
        merged_matches: list[Any] = []
        backend_names: list[str] = []

        for token in tokens:
            session = self._sessions[token]
            backend = session.get("backend", "")
            if backend and backend not in backend_names:
                backend_names.append(backend)
            for match in session.get("matches", []):
                h = match.get("contentHash", "")
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    merged_matches.append(match)

        merged_backend = ", ".join(backend_names) if backend_names else ""
        merged_token = secrets.token_urlsafe(16)
        session_data: dict[str, Any] = {
            "matches": _sort_by_date(merged_matches),
            "backend": merged_backend,
            "httpPort": http_port,
            "querySummary": query_summary,
        }
        self._add_session(merged_token, session_data)
        return merged_token, session_data
