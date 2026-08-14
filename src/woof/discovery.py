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
import sys
import time
from collections.abc import Callable, Generator
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from filelock import FileLock
from filelock import Timeout as LockTimeout
from platformdirs import user_config_dir

__all__ = [
    "ActivityTracker",
    "DiscoveryInfo",
    "LockState",
    "LockTimeout",
    "acquire_startup_lock",
    "describe_lock_state",
    "discovery_path",
    "generate_token",
    "is_pid_alive",
    "lock_owner_path",
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

        with startup_lock().acquire(timeout=30.0):
            ...

    Prefer `acquire_startup_lock` over calling this directly — it additionally
    records which process holds the lock, which `describe_lock_state` needs.
    """
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    return FileLock(str(_LOCK_FILE))


def lock_owner_path() -> Path:
    """Sidecar file recording the pid currently holding the startup lock.

    The lock file itself is an anonymous OS-level lock (and on Windows is
    unlinked on release) — it carries no owner information a waiter could
    read. This sidecar exists purely so `describe_lock_state` can report
    *who* holds the lock, for diagnostics; it plays no part in the locking
    itself.
    """
    return _LOCK_FILE.with_name(_LOCK_FILE.name + ".owner")


@contextlib.contextmanager
def acquire_startup_lock(timeout: float) -> Generator[None, None, None]:
    """Acquire the startup lock for *timeout* seconds, recording this process as owner.

    Raises `LockTimeout` if the lock isn't acquired in time. Only the caller
    holding this lock may spawn a new Woof instance — a caller that fails to
    acquire it must wait for the holder's spawn instead of starting its own,
    or two competing instances get spawned. That means callers should expect
    to hold this for as long as their whole check-spawn-wait sequence takes,
    not just an initial check.
    """
    with startup_lock().acquire(timeout=timeout):
        owner_path = lock_owner_path()
        owner_path.write_text(str(os.getpid()), encoding="utf-8")
        try:
            yield
        finally:
            with contextlib.suppress(FileNotFoundError):
                owner_path.unlink()


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


if sys.platform == "win32":
    import ctypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259

    def is_pid_alive(pid: int) -> bool:
        """Best-effort liveness check; a false positive is caught by the port probe.

        ``os.kill(pid, 0)`` is *not* a safe existence check on Windows: signal
        ``0`` is ``CTRL_C_EVENT``, so it calls ``GenerateConsoleCtrlEvent``,
        which fails with ``ERROR_INVALID_PARAMETER`` whenever the caller has
        no console of its own — exactly how a bridge launched by a GUI MCP
        host (Claude Desktop, VS Code, etc.) normally runs. That made this
        always report "dead" for a perfectly live Woof, so bridges spun for
        the full startup timeout instead of finding it. Query the process
        handle directly instead.
        """
        handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == _STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

else:

    def is_pid_alive(pid: int) -> bool:
        """Best-effort liveness check; a false positive is caught by the port probe.

        If *pid* is our own child, ``os.kill(pid, 0)`` alone would report a
        zombie (exited but not yet reaped) as alive, so a non-blocking reap
        is attempted first.
        """
        with contextlib.suppress(ChildProcessError):
            reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
            if reaped_pid == pid:
                return False
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


@dataclass(frozen=True)
class LockState:
    """Snapshot of who (if anyone) currently holds the startup lock.

    ``holder_pid``/``holder_alive`` come from the `lock_owner_path` sidecar,
    not the OS lock itself, so they can be stale (e.g. an owner file left
    behind by a hard-killed process on a filesystem where cleanup didn't run)
    — that staleness is exactly the signal ``holder_alive=False`` is meant to
    surface.
    """

    lock_held: bool
    holder_pid: int | None
    holder_alive: bool | None
    discovery_pid: int | None
    discovery_alive: bool | None


def describe_lock_state() -> LockState:
    """Best-effort diagnostic snapshot of the startup lock and discovery file.

    Distinguishes "another bridge is actively spawning Woof right now"
    (lock held, owner pid alive) from "a lock/owner file was left behind by a
    process that no longer exists" (owner pid dead) — the only case where
    something might actually be stuck. Even then, an OS-level advisory lock
    is released automatically once its owning process exits, so this is
    read-only diagnostic information, not a signal to delete files by hand.
    """
    owner_pid: int | None
    try:
        owner_pid = int(lock_owner_path().read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        owner_pid = None
    holder_alive = is_pid_alive(owner_pid) if owner_pid is not None else None

    try:
        with startup_lock().acquire(timeout=0):
            lock_held = False
    except LockTimeout:
        lock_held = True

    info = read_discovery()
    discovery_alive = is_pid_alive(info.pid) if info is not None else None

    return LockState(
        lock_held=lock_held,
        holder_pid=owner_pid,
        holder_alive=holder_alive,
        discovery_pid=info.pid if info is not None else None,
        discovery_alive=discovery_alive,
    )


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
