"""Gallery session management for Woof.

Tracks search-result sessions keyed by URL-safe tokens.  The underlying
``sessions`` dict is shared with the HTTP server so it can serve
``/api/results/{token}`` without any additional coupling.
"""

from __future__ import annotations

import logging
import math
import secrets
from dataclasses import dataclass, field
from typing import Any

from woof.agent_client import AgentClient, AgentError
from woof.config import LibraryConfig

_log = logging.getLogger(__name__)
_MAX_SESSIONS = 100


class PageOutOfRange(Exception):
    """Raised when the requested page index exceeds the session's page count."""


@dataclass
class SessionHandler:
    """Single session data"""

    library: LibraryConfig | None
    agent: AgentClient | None
    queryArgs: dict[str, Any]
    pageSize: int
    totalCount: int
    page: int = 0
    matches: list[Any] = field(default_factory=list)  # Matches of the current server page

    def transfert_object(self) -> dict[str, Any]:
        """Return a JSON-serializable dict representation of this session on the client Gallery."""

        return {"matches": self.matches, "totalCount": self.totalCount, "pageSize": self.pageSize}

    async def fetch_page(self, page: int) -> bool:
        """Fetch Wally server page *page* into this session via *agent*."""
        if self.page == page:
            return True  # No-op
        if page >= math.ceil(self.totalCount / self.pageSize):
            raise PageOutOfRange(
                "page {page} out of range for totalCount="
                f"{self.totalCount}, pageSize={self.pageSize}"
            )
        if self.library is None:
            _log.error("fetch_page: session has no library")
            return False
        if self.agent is None:
            _log.error("fetch_page: session has no agentClient")
            return False
        args = {**self.queryArgs, "page": page}
        try:
            result = await self.agent.call_tool("wally", "search_photos", args, self.library)
        except AgentError as exc:
            _log.error("fetch_page(%d) Wally call failed: %s", page, exc)
            return False
        stamped = [{**m, "library": self.library.name} for m in result.get("matches", [])]
        self.matches = stamped
        self.page = page
        return True


@dataclass
class ChainedSessionHandler(SessionHandler):
    """SessionHandler for merged sessions chaining several Sessions"""

    # Set of sessions in case of a merge of multi-page sessions
    chainedSessions: list[SessionHandler] = field(default_factory=list)

    def transfert_object(self) -> dict[str, Any]:
        base = super().transfert_object()
        base["pageMap"] = [
            {
                "pageSize": s.pageSize,
                "pageCount": math.ceil(s.totalCount / s.pageSize),
                "totalCount": s.totalCount,
            }
            for s in self.chainedSessions
        ]
        return base

    async def fetch_page(self, page: int) -> bool:
        # Find the right session for a session of type 'set'
        page_in_session = page
        for s in self.chainedSessions:
            num_session_pages = math.ceil(
                s.totalCount / s.pageSize
            )  # number of pages for 0-indexed
            if page_in_session < num_session_pages:
                if await s.fetch_page(page_in_session):
                    self.matches = s.matches
                    self.page = page
                    return True
                return False
            page_in_session -= num_session_pages
        raise PageOutOfRange(
            f"page {page} out of range for chained session with totalCount={self.totalCount}"
        )


class GallerySessionManager:
    """Stores and retrieves gallery sessions keyed by URL-safe tokens.

    The ``sessions`` property exposes the underlying dict so the HTTP server
    can share it by reference without importing this class.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionHandler] = {}  # insertion-ordered (Python 3.7+)

    def _add_session(self, token: str, data: SessionHandler) -> None:
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
        library: LibraryConfig,
        agent: AgentClient | None,
        query_args: dict[str, Any],
        page_size: int,
        total_count: int | None = None,
        page: int = 0,
        matches: list[Any] | None = None,
    ) -> str:
        """Store a new search-result session and return its token.

        Args:
            library: Library the matches belong to.
            query_args: Wally search arguments (used for server-page fetching).
            page_size: Number of matches per Wally server page.
            total_count: Wally's reported total for the query (may exceed
                ``len(matches)`` when results are paginated or capped).
                Defaults to ``len(matches)`` when omitted.
            page: 0-indexed current server page.
            matches: Photo match records from a Wally search result.
        """
        token = secrets.token_urlsafe(16)
        if matches is None:
            matches = []
        stamped = [{**m, "library": library.name} for m in matches]
        self._add_session(
            token,
            SessionHandler(
                library=library,
                agent=agent,
                queryArgs=query_args,
                pageSize=page_size,
                totalCount=total_count if total_count is not None else len(stamped),
                page=page,
                matches=stamped,
            ),
        )
        return token

    def get(self, token: str, page: int = 0) -> SessionHandler | None:
        """Return the session for *token*, or ``None`` if not found
            and the effective page index.
        If session is of type 'set', the sub-session at *page* is selected
        """
        return self._sessions.get(token)

    def unknown_tokens(self, tokens: list[str]) -> list[str]:
        """Return the subset of *tokens* not present in the session store."""
        return [t for t in tokens if t not in self._sessions]

    def merge(
        self,
        tokens: list[str],
    ) -> tuple[str, SessionHandler]:
        """Merge sessions from *tokens* into a new session and return it.

        Single-session merge:
            Inherits ``queryContext`` and ``totalCount`` from the source so
            Wally server-page navigation continues to work in the gallery.

        Multi-session merge — small (total loaded matches < ``_SERVER_PAGE_SIZE``):
            Flattens all matches into one session, deduplicated by
            ``contentHash`` in first-seen order.  ``queryContext`` is ``None``
            because there is no single Wally query to paginate further.

        Multi-session merge — large (total loaded matches >= ``_SERVER_PAGE_SIZE``):
            Chains the source sessions as server pages.  The merged session
            starts at page 0 (first source session's matches).  ``queryContext``
            carries ``type: "chained"`` so the HTTP server can serve subsequent
            pages via :meth:`load_chained_page` without calling Wally.
            ``totalCount`` is the sum of loaded matches across all sources.

        Each match already carries its ``"library"`` field from :meth:`create`.
        Assumes all *tokens* are valid — call :meth:`unknown_tokens` first.

        Returns:
            ``(merged_token, session_data)`` where *session_data* has the
            standard session shape described on this class.
        """
        merged_token = secrets.token_urlsafe(16)
        if len(tokens) == 0:
            raise ValueError("nothing to merge")
        elif len(tokens) == 1:
            src = self._sessions[tokens[0]]
            return tokens[0], src
        else:
            chained_sessions = [self._sessions[t] for t in tokens]
            if len(chained_sessions) == 0:
                raise ValueError("no sessions found")
            total_count = sum(s.totalCount for s in chained_sessions)
            if total_count <= chained_sessions[0].pageSize:
                # Merge matches with deduplicatation
                seen_hashes: set[str] = set()
                merged_matches: list[Any] = []
                for token in tokens:
                    for match in self._sessions[token].matches:
                        h = match.get("contentHash", "")
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            merged_matches.append(match)
                session_data = SessionHandler(
                    library=None,
                    agent=chained_sessions[0].agent,
                    queryArgs={},
                    pageSize=chained_sessions[0].pageSize,
                    totalCount=len(merged_matches),
                    matches=merged_matches,
                )
            else:
                first_src = self._sessions[tokens[0]]
                session_data = ChainedSessionHandler(
                    library=None,
                    agent=chained_sessions[0].agent,
                    queryArgs={},
                    pageSize=first_src.pageSize,
                    totalCount=total_count,
                    chainedSessions=chained_sessions,
                    matches=first_src.matches,
                )

        self._add_session(merged_token, session_data)
        return merged_token, session_data
