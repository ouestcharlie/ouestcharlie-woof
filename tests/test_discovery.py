"""Tests for woof.discovery: discovery file, startup lock, staleness, idle tracking."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

from woof import discovery


@pytest.fixture(autouse=True)
def _isolated_discovery_paths(tmp_path, monkeypatch):
    """Redirect discovery/lock paths to a per-test tmp dir so tests never touch
    the real ~/.config/ouestcharlie files or interfere with each other."""
    monkeypatch.setattr(discovery, "_APP_DIR", tmp_path)
    monkeypatch.setattr(discovery, "_DISCOVERY_FILE", tmp_path / "woof-discovery.json")
    monkeypatch.setattr(discovery, "_LOCK_FILE", tmp_path / "woof-discovery.lock")
    yield


def test_write_and_read_discovery_roundtrip() -> None:
    info = discovery.DiscoveryInfo(pid=1234, port=5678, token="tok")
    discovery.write_discovery(info)
    assert discovery.read_discovery() == info


def test_write_discovery_sets_owner_only_permissions() -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX file mode bits not meaningful on Windows")
    discovery.write_discovery(discovery.DiscoveryInfo(pid=1, port=2, token="t"))
    mode = discovery.discovery_path().stat().st_mode & 0o777
    assert mode == 0o600


def test_read_discovery_missing_file_returns_none() -> None:
    assert discovery.read_discovery() is None


def test_read_discovery_corrupt_file_returns_none() -> None:
    discovery.discovery_path().parent.mkdir(parents=True, exist_ok=True)
    discovery.discovery_path().write_text("not json", encoding="utf-8")
    assert discovery.read_discovery() is None


def test_remove_discovery_is_idempotent() -> None:
    discovery.remove_discovery()  # no file yet — must not raise
    discovery.write_discovery(discovery.DiscoveryInfo(pid=1, port=2, token="t"))
    discovery.remove_discovery()
    assert discovery.read_discovery() is None


def test_is_pid_alive_true_for_current_process() -> None:
    assert discovery.is_pid_alive(os.getpid()) is True


def test_is_pid_alive_false_for_bogus_pid() -> None:
    # A pid extremely unlikely to exist.
    assert discovery.is_pid_alive(2**30) is False


def test_server_url_property() -> None:
    info = discovery.DiscoveryInfo(pid=1, port=9999, token="t")
    assert info.server_url == "http://127.0.0.1:9999"


def test_spawn_log_path_is_under_app_dir() -> None:
    assert discovery.spawn_log_path().parent == discovery._APP_DIR
    assert discovery.spawn_log_path().name == "woof-spawn.log"


@pytest.mark.asyncio
async def test_probe_alive_false_when_pid_dead() -> None:
    info = discovery.DiscoveryInfo(pid=2**30, port=1, token="t")
    assert await discovery.probe_alive(info) is False


@pytest.mark.asyncio
async def test_probe_alive_true_when_healthz_responds_ok() -> None:
    info = discovery.DiscoveryInfo(pid=os.getpid(), port=1, token="t")
    mock_response = AsyncMock()
    mock_response.status_code = 200
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        assert await discovery.probe_alive(info) is True


@pytest.mark.asyncio
async def test_probe_alive_false_when_healthz_errors() -> None:
    import httpx

    info = discovery.DiscoveryInfo(pid=os.getpid(), port=1, token="t")
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("refused")):
        assert await discovery.probe_alive(info) is False


def test_startup_lock_serializes_second_acquire() -> None:
    lock_a = discovery.startup_lock()
    lock_b = discovery.startup_lock()
    with (
        lock_a.acquire(timeout=1.0),
        pytest.raises(discovery.LockTimeout),
        lock_b.acquire(timeout=0.2),
    ):
        pass  # pragma: no cover — must time out before entering


def test_acquire_startup_lock_writes_and_clears_owner_pid() -> None:
    with discovery.acquire_startup_lock(timeout=1.0):
        assert discovery.lock_owner_path().read_text(encoding="utf-8") == str(os.getpid())
    assert not discovery.lock_owner_path().exists()


def test_acquire_startup_lock_clears_owner_pid_even_on_error() -> None:
    with pytest.raises(RuntimeError), discovery.acquire_startup_lock(timeout=1.0):
        raise RuntimeError("boom")
    assert not discovery.lock_owner_path().exists()


def test_acquire_startup_lock_raises_lock_timeout_when_contended() -> None:
    with (
        discovery.startup_lock().acquire(timeout=1.0),
        pytest.raises(discovery.LockTimeout),
        discovery.acquire_startup_lock(timeout=0.2),
    ):
        pass  # pragma: no cover — must time out before entering


def test_describe_lock_state_when_nothing_running() -> None:
    state = discovery.describe_lock_state()
    assert state == discovery.LockState(
        lock_held=False,
        holder_pid=None,
        holder_alive=None,
        discovery_pid=None,
        discovery_alive=None,
    )


def test_describe_lock_state_reports_held_lock_and_live_owner() -> None:
    with discovery.acquire_startup_lock(timeout=1.0):
        state = discovery.describe_lock_state()

    assert state.lock_held is True
    assert state.holder_pid == os.getpid()
    assert state.holder_alive is True


def test_describe_lock_state_reports_stale_owner_file() -> None:
    # A leftover owner file from a process that no longer exists, with no
    # live OS-level lock held (e.g. that process crashed and the OS already
    # released its lock, but our sidecar file survived).
    discovery.lock_owner_path().write_text(str(2**30), encoding="utf-8")

    state = discovery.describe_lock_state()

    assert state.lock_held is False
    assert state.holder_pid == 2**30
    assert state.holder_alive is False


def test_describe_lock_state_reports_discovery_pid_liveness() -> None:
    discovery.write_discovery(discovery.DiscoveryInfo(pid=os.getpid(), port=1, token="t"))
    state = discovery.describe_lock_state()
    assert state.discovery_pid == os.getpid()
    assert state.discovery_alive is True

    discovery.write_discovery(discovery.DiscoveryInfo(pid=2**30, port=1, token="t"))
    state = discovery.describe_lock_state()
    assert state.discovery_pid == 2**30
    assert state.discovery_alive is False


def test_activity_tracker_idle_seconds_increases_and_resets() -> None:
    tracker = discovery.ActivityTracker()
    first = tracker.idle_seconds()
    assert first >= 0
    tracker.touch()
    assert tracker.idle_seconds() < first + 1  # touch resets the clock


@pytest.mark.asyncio
async def test_watch_idle_requests_shutdown_once_idle_timeout_exceeded() -> None:
    tracker = discovery.ActivityTracker()
    requested = []

    await discovery.watch_idle(
        tracker,
        idle_timeout=0.05,
        check_interval=0.02,
        should_exit=lambda: False,
        request_shutdown=lambda: requested.append(True),
    )

    assert requested == [True]


@pytest.mark.asyncio
async def test_watch_idle_does_not_shut_down_while_touches_keep_arriving() -> None:
    tracker = discovery.ActivityTracker()
    requested = []
    checks = {"n": 0}

    def should_exit() -> bool:
        # Simulate keepalives resetting the tracker on every poll, then stop
        # the loop ourselves after a few iterations (the real caller stops it
        # by shutting down uvicorn; here should_exit() doubles as our "enough
        # iterations happened without a spurious shutdown" signal).
        checks["n"] += 1
        if checks["n"] < 5:
            tracker.touch()
            return False
        return True

    await discovery.watch_idle(
        tracker,
        idle_timeout=0.05,
        check_interval=0.01,
        should_exit=should_exit,
        request_shutdown=lambda: requested.append(True),
    )

    assert requested == []


@pytest.mark.asyncio
async def test_watch_idle_returns_immediately_when_should_exit_already_true() -> None:
    tracker = discovery.ActivityTracker()
    requested = []

    await discovery.watch_idle(
        tracker,
        idle_timeout=0.05,
        check_interval=0.01,
        should_exit=lambda: True,
        request_shutdown=lambda: requested.append(True),
    )

    assert requested == []
