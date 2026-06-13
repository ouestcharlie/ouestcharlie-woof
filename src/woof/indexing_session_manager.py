"""Indexing session manager for background index_library runs."""

from __future__ import annotations

import asyncio
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
        self._tasks: dict[str, asyncio.Task] = {}
        self._max_sessions = max_sessions

    def start(self, library_name: str, partition: str) -> str:
        """Create a new running session and return its session_id."""
        while len(self._sessions) >= self._max_sessions:
            evicted = next(iter(self._sessions))
            del self._sessions[evicted]
            self._tasks.pop(evicted, None)
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

    def update(self, session_id: str, progress: float, total: float, message: str) -> bool:
        """Update progress fields for an in-progress session."""
        session = self._sessions.get(session_id)
        if session is None:
            _log.warning(f"Cannot update session '{session_id}' because it is not found")
            return False
        session["progress"] = progress
        session["total"] = total
        session["message"] = message
        return True

    def complete(self, session_id: str, summary: Any) -> bool:
        """Mark a session as completed and store its summary."""
        session = self._sessions.get(session_id)
        if session is None:
            _log.warning(f"Cannot set complete on session '{session_id}' because it is not found")
            return False
        session["status"] = "completed"
        session["summary"] = summary
        _log.debug(f"Session '{session_id} is completed")
        return True

    def fail(self, session_id: str, error: str) -> bool:
        """Mark a session as failed and store the error message."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session["status"] = "failed"
        session["error"] = error
        _log.debug(f"Session '{session_id} has failed")
        return True

    def register_task(self, session_id: str, task: asyncio.Task) -> None:
        """Associate an asyncio Task with a session so it can be cancelled."""
        self._tasks[session_id] = task

    def cancel(self, session_id: str) -> bool:
        """Request cancellation of a running session.

        Transitions status to "cancelling" and calls task.cancel().
        Returns False if the session is unknown or not in "running" state.
        """
        session = self._sessions.get(session_id)
        if session is None or session["status"] != "running":
            _log.warning(
                f"Cannot cancel session '{session_id}'"
                "because it is not found or not in running state"
            )
            return False
        session["status"] = "cancelling"
        task = self._tasks.get(session_id)
        if task:
            task.cancel()
            _log.debug(f"Session '{session_id} being cancelled")
            return True
        _log.warning(f"Session '{session_id} not cancelled since it is missing a task")
        return False

    def cancelled(self, session_id: str) -> bool:
        """Mark a session as cancelled (called when CancelledError is received)."""
        session = self._sessions.get(session_id)
        if session is None:
            _log.warning(f"Cannot set cancelled session '{session_id}' because it is not found")
            return False
        session["status"] = "cancelled"
        return True

    def get(self, session_id: str) -> dict[str, Any] | None:
        """Return the session dict, or None if not found."""
        return self._sessions.get(session_id)
