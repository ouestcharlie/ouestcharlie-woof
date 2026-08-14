"""Thin stdio↔HTTP bridge for Woof's HTTP-mode transport.

MCP hosts (Claude Desktop, Goose, etc.) still spawn one stdio subprocess per
connection. Rather than each of those being a full Woof instance each spawned
process is now this small bridge:

1. Discover (or lazily start) the one persistent Woof HTTP instance.
2. Relay MCP JSON-RPC messages between this process's stdio and Woof's
   ``/mcp`` endpoint, unmodified — no protocol re-implementation, just a
   transport-level pump between two ``SessionMessage`` stream pairs.
3. Send periodic ``/keepalive`` pings for as long as the host keeps this
   bridge connection open, so Woof knows this connection is still active.

No Node.js / ``mcp-remote`` dependency — pure Python using the same MCP SDK
primitives Woof already depend on.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from typing import Any

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.streamable_http import streamable_http_client
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage

from . import discovery
from .discovery import (
    DiscoveryInfo,
    LockTimeout,
    is_pid_alive,
    probe_alive,
    read_discovery,
    remove_discovery,
    spawn_log_path,
)
from .logging_setup import setup_logging

_log = logging.getLogger(__name__)

# How long to wait for a lazily-spawned Woof to finish booting (heavy module
# imports, DB/index setup) before giving up. Overridable for slow machines —
# see WOOF_SPAWN_WAIT_SECONDS.
_SPAWN_WAIT_SECONDS = float(os.environ.get("WOOF_SPAWN_WAIT_SECONDS", "45.0"))

# The startup lock is held for the *entire* check-spawn-wait sequence below,
# not just the check — only the lock holder is allowed to call _spawn_woof(),
# so a bridge that can't get the lock must never spawn on its own, only poll
# for the holder's discovery file. That means a waiting bridge has to be
# willing to sit through another bridge's full spawn wait, plus a little
# slack so it isn't kicked out right as the holder is about to finish.
_LOCK_TIMEOUT_SECONDS = _SPAWN_WAIT_SECONDS + 5.0

_KEEPALIVE_INTERVAL_SECONDS = 60.0


async def _wait_for_discovery(timeout: float) -> DiscoveryInfo:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = read_discovery()
        if info is not None and await probe_alive(info):
            return info
        await asyncio.sleep(0.2)
    raise TimeoutError("Timed out waiting for Woof to become ready")


def _spawn_woof() -> None:
    """Launch Woof as a detached background process in HTTP mode.

    stdout/stderr are captured to ``spawn_log_path()`` rather than discarded —
    a crash before Woof's own ``setup_logging()`` takes over ``sys.stderr``
    (e.g. an early import error) would otherwise be completely invisible.
    """
    env = {**os.environ, "WOOF_TRANSPORT": "http"}
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    log_path = spawn_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            [sys.executable, "-m", "woof"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            **kwargs,
        )


async def ensure_woof_running() -> DiscoveryInfo:
    """Return a live Woof instance's discovery info, lazily starting one if needed.

    Serialized by an advisory file lock so concurrent bridges (e.g. CoWork
    opening more than one connection at once) never spawn competing
    instances — the second bridge blocks until the first has either
    confirmed an existing instance or finished spawning + writing a fresh
    discovery file. The lock must be held for the *whole* wait, not just the
    initial check: only the lock holder is allowed to call _spawn_woof(), so
    a bridge that loses the lock race has to sit and poll rather than assume
    no one else is spawning and start its own competing instance.
    """
    try:
        with discovery.acquire_startup_lock(_LOCK_TIMEOUT_SECONDS):
            info = read_discovery()
            if info is not None and await probe_alive(info):
                return info
            if info is not None:
                _log.info("Stale discovery file (pid=%s); removing", info.pid)
                remove_discovery()
            _log.info("No live Woof instance found; starting one (spawn log: %s)", spawn_log_path())
            _spawn_woof()
            return await _wait_for_discovery(_SPAWN_WAIT_SECONDS)
    except LockTimeout:
        # Another bridge is mid-startup; give it a chance to finish rather
        # than failing outright. Never spawns here — only the lock holder
        # spawns, to avoid a competing second Woof instance.
        return await _wait_for_discovery(_SPAWN_WAIT_SECONDS)


async def _pump(
    src: MemoryObjectReceiveStream[SessionMessage | Exception],
    dst: MemoryObjectSendStream[SessionMessage],
) -> None:
    async for message in src:
        if isinstance(message, Exception):
            # A malformed/unparseable message at the transport layer — not a
            # valid SessionMessage to relay. A full ClientSession/ServerSession
            # would normally surface this to its caller; here we just log and
            # drop it, since raw pumping has no protocol-level party to report to.
            _log.warning("Dropping malformed message during relay: %s", message)
            continue
        await dst.send(message)


async def _keepalive_loop(client: httpx.AsyncClient) -> None:
    while True:
        await asyncio.sleep(_KEEPALIVE_INTERVAL_SECONDS)
        try:
            await client.post("/keepalive")
        except httpx.HTTPError as exc:
            _log.debug("Keepalive ping failed (Woof may be restarting): %s", exc)


async def run_bridge() -> None:
    info = await ensure_woof_running()
    headers = {"Authorization": f"Bearer {info.token}"}

    async with (
        httpx.AsyncClient(
            base_url=info.server_url,
            headers=headers,
            follow_redirects=True,
            # streamable_http_client() would normally build its own client with
            # generous MCP-appropriate timeouts (30s connect, 5min read) — but
            # passing our own httpx_client (below, so /keepalive can share it)
            # opts out of that and falls back to httpx's bare 5s-everything
            # default, which cuts off any tool call slower than 5s (e.g. a
            # lazily-spawned Wally sidecar taking ~20s to come up). Match the
            # SDK's own recommended defaults instead of inheriting httpx's.
            timeout=httpx.Timeout(30.0, read=300.0),
        ) as http_client,
        stdio_server() as (host_read, host_write),
        # Trailing slash: Woof mounts its MCP app at "/mcp" via Starlette's
        # Mount, which 307-redirects the bare "/mcp" to "/mcp/" — request the
        # canonical form directly rather than relying on redirect-following
        # (follow_redirects=True above is still set as a defensive backstop).
        streamable_http_client(f"{info.server_url}/mcp/", http_client=http_client) as (
            woof_read,
            woof_write,
            _get_session_id,
        ),
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(_pump, host_read, woof_write)
        tg.start_soon(_pump, woof_read, host_write)
        tg.start_soon(_keepalive_loop, http_client)


async def stop_running_instance() -> bool:
    """Best-effort ``woof --stop``: authenticated shutdown request + cleanup.

    Returns True if a running instance was found and asked to stop.
    """
    info = read_discovery()
    if info is None:
        _log.info("No discovery file found; nothing to stop")
        return False
    if not is_pid_alive(info.pid):
        _log.info("Discovery file is stale (pid %d not running); cleaning up", info.pid)
        remove_discovery()
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{info.server_url}/shutdown",
                headers={"Authorization": f"Bearer {info.token}"},
            )
    except httpx.HTTPError as exc:
        _log.warning("Shutdown request failed (%s); removing discovery file anyway", exc)
    remove_discovery()
    return True


def _print_lock_diagnosis() -> None:
    """Human-readable dump of `discovery.describe_lock_state`, for ``--diagnose``."""
    state = discovery.describe_lock_state()

    print(f"startup lock: {'HELD' if state.lock_held else 'free'}")
    if state.holder_pid is not None:
        liveness = "alive" if state.holder_alive else "NOT running — stale owner file"
        print(f"  owner pid: {state.holder_pid} ({liveness})")
    elif state.lock_held:
        print("  owner pid: unknown (lock held, but no owner file found)")

    if state.discovery_pid is not None:
        liveness = "alive" if state.discovery_alive else "NOT running — stale discovery file"
        print(f"discovery file: pid={state.discovery_pid} ({liveness})")
    else:
        print("discovery file: none")


def main() -> None:
    setup_logging("woof-bridge", log_file_env_var="WOOF_BRIDGE_LOG_FILE", level=logging.DEBUG)
    # filelock logs every ~0.05s poll retry at DEBUG while contended, which
    # drowns out the rest of a bridge's log during a slow/contended startup.
    logging.getLogger("filelock").setLevel(logging.WARNING)

    argv = sys.argv[1:]
    if "--stop" in argv:
        asyncio.run(stop_running_instance())
        return
    if "--diagnose" in argv:
        _print_lock_diagnosis()
        return
    try:
        asyncio.run(run_bridge())
    except TimeoutError as exc:
        _log.error("Woof did not become ready in time: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
