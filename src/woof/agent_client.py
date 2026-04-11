"""MCP client for launching and calling Whitebeard and Wally as child processes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sys
from typing import Any

import httpx
from fastmcp import Context
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .config import BackendConfig

_log = logging.getLogger(__name__)


class AgentError(Exception):
    """Raised when an agent tool call fails."""


_SIDECAR_INIT_TIMEOUT = 30.0  # seconds before sidecar startup is considered hung

# Sentinel placed in the call queue to signal the session loop to stop.
_STOP = object()


async def _read_wally_ready(stdout: asyncio.StreamReader) -> int:
    """Read Wally's stdout until WALLY_READY port=<n>."""
    async for raw in stdout:
        line = raw.decode().strip()
        if line.startswith("WALLY_READY"):
            parts = dict(p.split("=") for p in line.split()[1:])
            return int(parts["port"])
    raise RuntimeError("Wally subprocess exited before reporting WALLY_READY")


async def _wait_for_port(port: int, *, retries: int = 20, delay: float = 0.1) -> None:
    """Probe *port* until a TCP connection succeeds.

    Wally prints WALLY_READY as soon as it knows its port, but uvicorn may
    not have finished binding the socket yet (especially on Linux).  Without
    this check, the immediately-following streamable_http_client open raises
    ConnectError on the first attempt.
    """
    for attempt in range(retries):
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            return
        except (ConnectionRefusedError, OSError) as err:
            if attempt == retries - 1:
                raise RuntimeError(
                    f"Wally port {port} did not accept connections after {retries} attempts"
                ) from err
            await asyncio.sleep(delay)


class _WallySidecar:
    """Persistent Wally subprocess with a dedicated asyncio task owning all MCP contexts.

    All anyio context managers (streamable_http_client, ClientSession) live in
    _session_loop, a single asyncio Task.  They are entered and exited in LIFO
    order within that task, which eliminates the "exit cancel scope in a different
    task" error that occurs when AsyncExitStack defers their cleanup across tasks.

    Tool calls are dispatched via asyncio.Queue; results flow back via per-call
    asyncio.Future objects.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._call_queue: asyncio.Queue = asyncio.Queue()
        self._ready_event: asyncio.Event = asyncio.Event()
        self._start_error: BaseException | None = None
        self.alive = False
        self.http_port: int | None = None
        self.token: str | None = None

    async def start(self, command: list[str], env: dict[str, str]) -> None:
        self._task = asyncio.create_task(self._session_loop(command, env), name="wally-sidecar")
        await asyncio.wait_for(self._ready_event.wait(), timeout=_SIDECAR_INIT_TIMEOUT)
        if self._start_error is not None:
            raise self._start_error

    async def stop(self) -> None:
        self.alive = False
        if self._task is not None and not self._task.done():
            await self._call_queue.put(_STOP)
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await self._task

    async def call_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        progress_cb: Any = None,
    ) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self._call_queue.put((tool_name, args, progress_cb, future))
        return await future

    async def _session_loop(self, command: list[str], env: dict[str, str]) -> None:
        """Own the entire Wally session lifetime: subprocess + HTTP + MCP session.

        All anyio context managers are nested here so cancel scopes are entered
        and exited in the same task.
        """
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=None,  # inherit → goes to system log
            )
            try:
                port = await asyncio.wait_for(
                    _read_wally_ready(proc.stdout),  # type: ignore[arg-type]
                    timeout=_SIDECAR_INIT_TIMEOUT,
                )
            except Exception as exc:
                self._start_error = exc
                self._ready_event.set()
                return

            self.http_port = port
            try:
                await _wait_for_port(port)
            except Exception as exc:
                self._start_error = exc
                self._ready_event.set()
                return

            token = env.get("WOOF_AGENT_TOKEN", "")
            self.token = token or None
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            url = f"http://127.0.0.1:{port}/mcp"

            # All three context managers stay in this task — cancel scopes are
            # entered and exited in LIFO order within the same asyncio task.
            async with (
                httpx.AsyncClient(headers=headers, timeout=None) as http_client,
                streamable_http_client(url, http_client=http_client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                try:
                    await session.initialize()
                except Exception as exc:
                    self._start_error = exc
                    self._ready_event.set()
                    return

                self.alive = True
                self._ready_event.set()
                _log.info("Wally sidecar ready (port=%d)", port)

                # Serve tool calls until the stop sentinel or an exception.
                while True:
                    item = await self._call_queue.get()
                    if item is _STOP:
                        break
                    tool_name, args, progress_cb, future = item
                    try:
                        result = await session.call_tool(
                            tool_name, args, progress_callback=progress_cb
                        )
                        if not future.done():
                            future.set_result(result)
                    except Exception as exc:
                        if not future.done():
                            future.set_exception(exc)

        except BaseException as exc:
            if not self._ready_event.is_set():
                self._start_error = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                self._ready_event.set()
            _log.debug("Wally session loop exited: %s", exc)
        finally:
            self.alive = False
            self._drain_queue()
            if proc is not None and proc.returncode is None:
                proc.terminate()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)

    def _drain_queue(self) -> None:
        """Fail any tool-call futures still waiting in the queue."""
        err = AgentError("Wally sidecar died")
        while True:
            try:
                item = self._call_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _STOP:
                continue
            _, _, _, future = item
            if not future.done():
                future.set_exception(err)


class AgentClient:
    """Launches agents as child processes and calls their MCP tools.

    Whitebeard: spawned fresh per call (indexing is infrequent and stateless).
    Wally: kept as a persistent sidecar (Streamable HTTP MCP transport).
    """

    def __init__(self) -> None:
        self._wally_sidecars: dict[str, _WallySidecar] = {}
        self._sidecar_init_lock: asyncio.Lock | None = None  # created lazily

    def get_wally_http_port(self, backend_name: str) -> int | None:
        """Return the HTTP port of the live Wally sidecar for *backend_name*, or None."""
        sidecar = self._wally_sidecars.get(backend_name)
        return sidecar.http_port if sidecar and sidecar.alive else None

    def get_wally_token(self, backend_name: str) -> str | None:
        """Return the Bearer token for the live Wally sidecar for *backend_name*, or None."""
        sidecar = self._wally_sidecars.get(backend_name)
        return sidecar.token if sidecar and sidecar.alive else None

    async def shutdown(self) -> None:
        """Stop all persistent agent sidecars gracefully."""
        for sidecar in list(self._wally_sidecars.values()):
            await sidecar.stop()
        self._wally_sidecars.clear()

    async def call_tool(
        self,
        module: str,
        tool_name: str,
        args: dict[str, Any],
        backend: BackendConfig,
        *,
        progress_ctx: Context | None = None,
    ) -> Any:
        """Call an agent MCP tool.

        Wally calls use a persistent sidecar session (HTTP transport).
        Whitebeard calls spawn a fresh process per call (stdio transport).
        """
        progress_cb = _make_progress_forwarder(progress_ctx) if progress_ctx else None

        if module == "wally":
            return await self._call_wally(tool_name, args, backend, progress_cb)

        return await self._call_ephemeral(module, tool_name, args, backend, progress_cb)

    async def _call_wally(
        self,
        tool_name: str,
        args: dict[str, Any],
        backend: BackendConfig,
        progress_cb: Any,
    ) -> Any:
        sidecar = await self._get_wally_sidecar(backend)
        result = await sidecar.call_tool(tool_name, args, progress_cb)
        if result.isError:
            content = _extract_text(result.content)
            raise AgentError(f"wally.{tool_name} failed: {content}")
        raw = _extract_text(result.content)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def _get_wally_sidecar(self, backend: BackendConfig) -> _WallySidecar:
        """Return the live Wally sidecar for *backend*, starting it if needed."""
        if self._sidecar_init_lock is None:
            self._sidecar_init_lock = asyncio.Lock()

        async with self._sidecar_init_lock:
            sidecar = self._wally_sidecars.get(backend.name)
            if sidecar is None or not sidecar.alive:
                sidecar = _WallySidecar()
                env = self._build_env(backend)
                await sidecar.start([sys.executable, "-m", "wally"], env)
                self._wally_sidecars[backend.name] = sidecar
        return sidecar

    def _build_env(self, backend: BackendConfig) -> dict[str, str]:
        return {
            **os.environ,
            "WOOF_BACKEND_CONFIG": json.dumps(backend.to_agent_env()),
            "WOOF_AGENT_TOKEN": secrets.token_urlsafe(32),
            "WALLY_BACKEND_NAME": backend.name,
        }

    async def _call_ephemeral(
        self,
        module: str,
        tool_name: str,
        args: dict[str, Any],
        backend: BackendConfig,
        progress_cb: Any,
    ) -> Any:
        """Spawn a fresh child process, call the tool, and exit."""
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", module],
            env=self._build_env(backend),
        )
        _log.info("Launching %s → %s(%s)", module, tool_name, list(args))

        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
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
    """Return a progress_callback that relays agent progress to the caller's context."""

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
