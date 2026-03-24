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
from woof.http_server import start_http_server
from woof.server import WoofServer

_config = WoofConfig.load()
_agent = AgentClient()
_gallery_sessions: dict = {}


# Wally's HTTP port is discovered dynamically after sidecar init via get_http_port_tool.
# The wally_port_fn callable is evaluated on every preview request so port changes
# (e.g. after sidecar restart) are picked up automatically.
# For now previews are all routed through the first configured backend's sidecar.
def _wally_port_fn() -> int | None:
    if not _config.backends:
        return None
    return _agent.get_wally_http_port(_config.backends[0].name)


def _wally_token_fn() -> str | None:
    if not _config.backends:
        return None
    return _agent.get_wally_token(_config.backends[0].name)


_http_port = start_http_server(
    gallery_sessions=_gallery_sessions,
    wally_port_fn=_wally_port_fn,
    wally_token_fn=_wally_token_fn,
)
_server = WoofServer(_config, _http_port, agent_client=_agent, gallery_sessions=_gallery_sessions)

mcp = _server.mcp  # module-level name required by `mcp dev`


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
