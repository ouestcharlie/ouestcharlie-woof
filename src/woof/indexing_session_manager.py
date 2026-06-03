"""Indexing session manager for background index_library runs."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_MAX_SESSIONS = 20


class IndexingSessionManager:
    """Tracks background Whitebeard indexing runs keyed by session_id.

    No locking needed: serve_in_loop places HTTP and MCP on the same asyncio
    event loop, so all reads and mutations are single-threaded.
    """

    def __init__(self, max_sessions: int = _DEFAULT_MAX_SESSIONS) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._max_sessions = max_sessions

    def start(self, library_name: str, partition: str) -> str:
        """Create a new running session and return its session_id."""
        while len(self._sessions) >= self._max_sessions:
            del self._sessions[next(iter(self._sessions))]
        session_id = secrets.token_urlsafe(16)
        self._sessions[session_id] = {
            "session_id": session_id,
            "library_name": library_name,
            "partition": partition,
            "status": "running",
            "progress": 0.0,
            "total": 1.0,
            "message": "",
            "summary": None,
            "error": None,
            "started_at": datetime.now(UTC).isoformat(),
        }
        return session_id

    def update(self, session_id: str, progress: float, total: float, message: str) -> None:
        """Update progress fields for an in-progress session."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["progress"] = progress
        session["total"] = total
        session["message"] = message

    def complete(self, session_id: str, summary: Any) -> None:
        """Mark a session as completed and store its summary."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["status"] = "completed"
        session["summary"] = summary

    def fail(self, session_id: str, error: str) -> None:
        """Mark a session as failed and store the error message."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["status"] = "failed"
        session["error"] = error

    def get(self, session_id: str) -> dict[str, Any] | None:
        """Return the session dict, or None if not found."""
        return self._sessions.get(session_id)
