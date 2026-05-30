"""Entry point for Woof — OuEstCharlie central controller.

Woof runs as a stdio MCP server launched by an MCP-capable assistant.  It binds
a local HTTP port for gallery serving and media proxying, then runs FastMCP on
stdio.  Both the HTTP server and MCP server share the same asyncio event loop —
uvicorn starts as a task inside FastMCP's lifespan.

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
from woof.server import WoofServer

_config = WoofConfig.load()
_agent = AgentClient()
_session_manager = GallerySessionManager()
_server = WoofServer(_config, agent_client=_agent, session_manager=_session_manager)
_server.fetch_page_fn = _server.make_fetch_page_fn()

mcp = _server.mcp  # module-level name required by `mcp dev`


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
