"""MCP client for launching and calling Whitebeard and Wally as child processes."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from fastmcp import Context

from .config import BackendConfig

_log = logging.getLogger(__name__)


class AgentError(Exception):
    """Raised when an agent tool call fails."""


class AgentClient:
    """Launches an agent as a stdio child process and calls its MCP tools.

    Each call_tool() invocation spawns a fresh agent process, performs the MCP
    handshake, calls the requested tool, and exits.  For V1 this is sufficient
    since agents are stateless.

    Agent processes are found via sys.executable (the same Python as Woof),
    so whitebeard and wally must be installed in the same venv.
    """

    async def call_tool(
        self,
        module: str,
        tool_name: str,
        args: dict[str, Any],
        backend: BackendConfig,
        *,
        progress_ctx: Context | None = None,
    ) -> Any:
        """Launch *module* as a child process and call *tool_name* with *args*.

        Args:
            module: Python module to run (e.g. "whitebeard", "wally").
            tool_name: MCP tool name exposed by the agent.
            args: Tool arguments.
            backend: Backend config forwarded to the agent via env vars.
            progress_ctx: If provided, agent progress notifications are
                relayed to the caller's MCP context.

        Returns:
            Parsed tool result (dict or list).

        Raises:
            AgentError: If the agent reports a tool error.
        """
        env = {
            **os.environ,
            "WOOF_BACKEND_CONFIG": json.dumps(backend.to_agent_env()),
            "WOOF_AGENT_TOKEN": "",
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", module],
            env=env,
        )
        _log.info("Launching %s → %s(%s)", module, tool_name, list(args))

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                progress_cb = _make_progress_forwarder(progress_ctx) if progress_ctx else None
                result = await session.call_tool(tool_name, args, progress_callback=progress_cb)

        if result.isError:
            content = _extract_text(result.content)
            raise AgentError(f"{module}.{tool_name} failed: {content}")

        raw = _extract_text(result.content)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw


def _extract_text(content: list[Any]) -> str:
    """Return the text from the first text content block."""
    for block in content:
        if hasattr(block, "text"):
            return block.text
    return str(content)


def _make_progress_forwarder(ctx: Context) -> Any:
    """Return a progress_callback that relays agent progress to the caller's context.

    The MCP SDK calls this as progress_callback(progress, total, message) when
    the child agent sends a notifications/progress message.  We forward it via
    FastMCP's Context.report_progress() so Claude Desktop sees the progress.
    """
    async def _handler(progress: float, total: float | None, message: str | None) -> None:
        try:
            await ctx.report_progress(
                progress=progress,
                total=total if total is not None else 1.0,
                message=message or "",
            )
        except Exception:
            _log.debug("Failed to forward progress notification", exc_info=True)

    return _handler
