"""Gallery session management for Woof.

Tracks search-result sessions keyed by URL-safe tokens.  The underlying
``sessions`` dict is shared with the HTTP server so it can serve
``/api/results/{token}`` without any additional coupling.
"""

from __future__ import annotations

import math
import secrets
from dataclasses import asdict, dataclass, field
from typing import Any

_MAX_SESSIONS = 100


@dataclass
class SessionData:
    """Single session data"""

    type: str  # Type: 'single', 'merge' or 'set' (for chained session merge)
    libraryName: str
    queryArgs: dict[str, Any]
    pageSize: int
    totalCount: int
    # Set of sessions in case of a merge of multi-page sessions
    chainedSessions: list[SessionData] = field(default_factory=list)
    page: int = 0
    matches: list[Any] = field(default_factory=list)  # Matches of the current server page

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict representation of this session.

        ``chainedSessions`` is omitted — internal to the ``'set'`` type and
        would bloat responses with redundant match data from all sub-sessions.
        """
        d = asdict(self)
        d.pop("chainedSessions", None)
        return d

    def replace_page(
        self,
        matches: list[Any],
        page: int,
    ) -> None:
        """Replace the current matches with a new server page.

        Updates ``matches`` and ``page`` in place.
        """

        library_name = self.libraryName
        self.matches = [{**m, "library": library_name} for m in matches]
        self.page = page


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
        self._sessions: dict[str, SessionData] = {}  # insertion-ordered (Python 3.7+)

    def _add_session(self, token: str, data: SessionData) -> None:
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
        query_args: dict[str, Any],
        page_size: int,
        total_count: int | None = None,
        page: int = 0,
        matches: list[Any] | None = None,
    ) -> str:
        """Store a new search-result session and return its token.

        Args:
            library_name: Library the matches belong to.
            matches: Photo match records from a Wally search result.
            total_count: Wally's reported total for the query (may exceed
                ``len(matches)`` when results are paginated or capped).
                Defaults to ``len(matches)`` when omitted.
            query_context: Optional dict holding the parameters needed to fetch
                additional server pages on demand.  Expected keys:
                ``library_name``, ``args`` (Wally search args), ``page``
                (0-indexed current page), ``pageSize``.  ``None`` for sessions
                created by :meth:`merge` (browse_gallery), which cannot
                paginate further.
        """
        token = secrets.token_urlsafe(16)
        if matches is None:
            matches = []
        stamped = [{**m, "library": library_name} for m in matches]
        self._add_session(
            token,
            SessionData(
                type="single",
                libraryName=library_name,
                queryArgs=query_args,
                pageSize=page_size,
                totalCount=total_count if total_count is not None else len(stamped),
                page=page,
                matches=stamped,
            ),
        )
        return token

    def get(self, token: str, page: int = 0) -> tuple[SessionData | None, int]:
        """Return the session for *token*, or ``None`` if not found
            and the effective page index.
        If session is of type 'set', the sub-session at *page* is selected
        """
        session = self._sessions.get(token)
        if session is None:
            return None, 0
        elif session.type != "set":
            num_session_pages = math.ceil(session.totalCount / session.pageSize)
            if page < session.totalCount / session.pageSize:
                return session, page
            else:
                return None, 0
        else:
            # Find the right session for a session of type 'set'
            selected_session = None
            for s in session.chainedSessions:
                num_session_pages = math.ceil(
                    s.totalCount / s.pageSize
                )  # number of pages for 0-indexed
                if page < num_session_pages:
                    selected_session = s
                    break
                page -= num_session_pages
            return selected_session, page

    def unknown_tokens(self, tokens: list[str]) -> list[str]:
        """Return the subset of *tokens* not present in the session store."""
        return [t for t in tokens if t not in self._sessions]

    def merge(
        self,
        tokens: list[str],
    ) -> tuple[str, SessionData]:
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
                session_data = SessionData(
                    type="merge",
                    libraryName="__merge__",
                    queryArgs={},
                    pageSize=chained_sessions[0].pageSize,
                    totalCount=len(merged_matches),
                    matches=merged_matches,
                )
            else:
                first_src = self._sessions[tokens[0]]
                session_data = SessionData(
                    type="set",
                    libraryName="__merge__",
                    queryArgs={},
                    pageSize=first_src.pageSize,
                    totalCount=sum(len(self._sessions[t].matches) for t in tokens),
                    page=0,
                    chainedSessions=chained_sessions,
                    matches=first_src.matches,
                )

        self._add_session(merged_token, session_data)
        return merged_token, session_data
