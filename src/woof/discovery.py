"""Discovery file, startup lock, and idle-shutdown tracking for Woof's HTTP mode.

Woof runs as a single persistent HTTP server; any number of ``woof-bridge``
stdio processes may connect to it concurrently. This module lets those
independent bridge processes agree on — and safely start — exactly one Woof
instance, and lets a running Woof instance decide for itself when it's safe
to shut down.

Layout (both files live in ``platformdirs.user_config_dir("ouestcharlie")``):
  woof-discovery.json  — ``{"pid": <int>, "port": <int>, "token": <str>}``, mode 0600
  woof-discovery.lock   — advisory lock guarding the check-then-spawn sequence
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from filelock import FileLock
from filelock import Timeout as LockTimeout
from platformdirs import user_config_dir

__all__ = [
    "ActivityTracker",
    "DiscoveryInfo",
    "LOCK_TIMEOUT_SECONDS",
    "LockTimeout",
    "discovery_path",
    "generate_token",
    "is_pid_alive",
    "lock_path",
    "probe_alive",
    "read_discovery",
    "remove_discovery",
    "spawn_log_path",
    "startup_lock",
    "watch_idle",
    "write_discovery",
]

_log = logging.getLogger(__name__)

_APP_DIR = Path(user_config_dir("ouestcharlie"))
_DISCOVERY_FILE = _APP_DIR / "woof-discovery.json"
_LOCK_FILE = _APP_DIR / "woof-discovery.lock"

# How long a bridge waits to acquire the startup lock before giving up.
LOCK_TIMEOUT_SECONDS = 10.0

# Default HTTP health-check timeout when probing an existing instance.
_PROBE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class DiscoveryInfo:
    pid: int
    port: int
    token: str

    @property
    def server_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def discovery_path() -> Path:
    return _DISCOVERY_FILE


def lock_path() -> Path:
    return _LOCK_FILE


def spawn_log_path() -> Path:
    """Path capturing a lazily-spawned Woof instance's stdout/stderr.

    Only catches output written before Woof's own ``setup_logging()`` takes
    over ``sys.stderr`` (very early import/startup failures) — after that,
    Woof's own log file (``woof.log``) has everything. Without this, an early
    crash in a lazily-spawned instance would otherwise vanish into
    ``DEVNULL`` with no way to diagnose it.
    """
    return _APP_DIR / "woof-spawn.log"


def startup_lock() -> FileLock:
    """Advisory lock serializing the check-then-spawn sequence across bridges.

    Callers should use it as a context manager with a bounded timeout, e.g.::

        with startup_lock().acquire(timeout=LOCK_TIMEOUT_SECONDS):
            ...
    """
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    return FileLock(str(_LOCK_FILE))


def write_discovery(info: DiscoveryInfo) -> None:
    """Atomically write the discovery file, mode 0600."""
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = _DISCOVERY_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(asdict(info)), encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, _DISCOVERY_FILE)


def read_discovery() -> DiscoveryInfo | None:
    """Return the discovery file's contents, or None if missing/corrupt."""
    try:
        raw = _DISCOVERY_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
        return DiscoveryInfo(pid=int(data["pid"]), port=int(data["port"]), token=str(data["token"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        _log.warning("Discovery file %s is corrupt; treating as absent", _DISCOVERY_FILE)
        return None


def remove_discovery() -> None:
    with contextlib.suppress(FileNotFoundError):
        _DISCOVERY_FILE.unlink()


def is_pid_alive(pid: int) -> bool:
    """Best-effort liveness check; a false positive is caught by the port probe."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user — treat as alive.
        return True
    except OSError:
        return False
    return True


async def probe_alive(info: DiscoveryInfo, *, timeout: float = _PROBE_TIMEOUT_SECONDS) -> bool:
    """Confirm *info* really identifies a live, responsive Woof instance.

    Checks both the pid (cheap, catches "nothing is running here") and an
    authenticated HTTP health check (catches "the pid is alive but it's an
    unrelated process that happened to reuse this pid/port").
    """
    if not is_pid_alive(info.pid):
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{info.server_url}/healthz",
                headers={"Authorization": f"Bearer {info.token}"},
            )
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


class ActivityTracker:
    """Tracks the most recent activity time so a persistent server can self-shut-down.

    Any proxied request or bridge keepalive should call ``touch()``.  A
    background task periodically checks ``idle_seconds()`` against a
    threshold and requests shutdown once every connection has gone quiet.
    """

    def __init__(self) -> None:
        self._last_active = time.monotonic()

    def touch(self) -> None:
        self._last_active = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_active


async def watch_idle(
    tracker: ActivityTracker,
    *,
    idle_timeout: float,
    check_interval: float,
    should_exit: Callable[[], bool],
    request_shutdown: Callable[[], None],
) -> None:
    """Poll *tracker* and call ``request_shutdown()`` once idle past *idle_timeout*.

    Loops until either ``should_exit()`` reports true (something else is
    already stopping the server — e.g. ``/shutdown`` or a signal) or the
    tracker has been idle longer than *idle_timeout*, at which point this
    calls ``request_shutdown()`` once and returns. Extracted as a standalone,
    dependency-injected function (rather than inline in ``__main__.py``) so
    the idle-shutdown *decision* is unit-testable without a real uvicorn
    server or real wall-clock timeouts.
    """
    while not should_exit():
        await asyncio.sleep(check_interval)
        if tracker.idle_seconds() > idle_timeout:
            request_shutdown()
            return
