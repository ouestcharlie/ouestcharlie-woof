"""Entry point for Woof — OuEstCharlie central controller.

Woof runs as a stdio MCP server launched by Claude Desktop.  It starts a
local HTTP server for thumbnail delivery (in a daemon thread) and then runs
the FastMCP server on stdio until stdin closes.

For MCP Inspector / mcp dev:
    mcp dev src/woof/__main__.py

For production (stdio transport, launched by Claude Desktop):
    python -m woof   or   woof

Logs are written to ~/Library/Logs/ouestcharlie/woof.log (macOS) or to
WOOF_LOG_FILE if set.
"""

from __future__ import annotations

import logging

from ouestcharlie_toolkit import setup_logging

_log_file = setup_logging("woof", log_file_env_var="WOOF_LOG_FILE", level=logging.DEBUG)
logging.getLogger(__name__).info("Woof starting — log: %s", _log_file)

from woof.agent_client import AgentClient
from woof.config import WoofConfig
from woof.gallery_session_manager import GallerySessionManager
from woof.http_server import start_http_server
from woof.server import WoofServer

_config = WoofConfig.load()
_agent = AgentClient()
_session_manager = GallerySessionManager()


# Wally connection info is discovered dynamically after sidecar init.
# Called on every preview request so changes (e.g. after sidecar restart)
# are picked up automatically.
def _wally_connection_fn(backend_name: str) -> tuple[int | None, str | None]:
    return _agent.get_wally_connection(backend_name)


_http_port = start_http_server(
    session_manager=_session_manager,
    wally_connection_fn=_wally_connection_fn,
)
_server = WoofServer(_config, _http_port, agent_client=_agent, session_manager=_session_manager)

mcp = _server.mcp  # module-level name required by `mcp dev`


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
