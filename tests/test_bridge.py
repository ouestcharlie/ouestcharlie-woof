"""Tests for woof.bridge: lazy-start coordination and --stop, mocking discovery I/O
and process spawning so no real Woof instance is ever started."""

from __future__ import annotations

import logging
import os
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from woof import bridge, discovery


@pytest.fixture(autouse=True)
def _isolated_discovery_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "_APP_DIR", tmp_path)
    monkeypatch.setattr(discovery, "_DISCOVERY_FILE", tmp_path / "woof-discovery.json")
    monkeypatch.setattr(discovery, "_LOCK_FILE", tmp_path / "woof-discovery.lock")
    yield


def test_spawn_woof_captures_output_to_spawn_log_instead_of_devnull() -> None:
    with patch("subprocess.Popen") as popen:
        bridge._spawn_woof()

    assert popen.call_count == 1
    _args, kwargs = popen.call_args
    assert kwargs["stdout"] is kwargs["stderr"]
    assert kwargs["stdout"].name == str(discovery.spawn_log_path())


@pytest.mark.asyncio
async def test_ensure_woof_running_reuses_live_instance_without_spawning() -> None:
    info = discovery.DiscoveryInfo(pid=os.getpid(), port=1234, token="tok")
    discovery.write_discovery(info)

    with (
        patch("woof.bridge.probe_alive", new=AsyncMock(return_value=True)),
        patch("woof.bridge._spawn_woof") as spawn,
    ):
        result = await bridge.ensure_woof_running()

    assert result == info
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_woof_running_cleans_up_stale_file_and_spawns() -> None:
    stale = discovery.DiscoveryInfo(pid=2**30, port=1234, token="old")
    discovery.write_discovery(stale)
    fresh = discovery.DiscoveryInfo(pid=os.getpid(), port=5678, token="new")

    async def fake_probe(info: discovery.DiscoveryInfo) -> bool:
        return info == fresh

    def fake_spawn() -> None:
        discovery.write_discovery(fresh)

    with (
        patch("woof.bridge.probe_alive", side_effect=fake_probe),
        patch("woof.bridge._spawn_woof", side_effect=fake_spawn) as spawn,
    ):
        result = await bridge.ensure_woof_running()

    assert result == fresh
    spawn.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_woof_running_no_discovery_file_spawns_once() -> None:
    fresh = discovery.DiscoveryInfo(pid=os.getpid(), port=9999, token="new")

    async def fake_probe(info: discovery.DiscoveryInfo) -> bool:
        return info == fresh

    def fake_spawn() -> None:
        discovery.write_discovery(fresh)

    with (
        patch("woof.bridge.probe_alive", side_effect=fake_probe),
        patch("woof.bridge._spawn_woof", side_effect=fake_spawn) as spawn,
    ):
        result = await bridge.ensure_woof_running()

    assert result == fresh
    spawn.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_woof_running_holds_lock_across_the_wait() -> None:
    """Regression test: an earlier version of this fix released the startup
    lock right after calling _spawn_woof(), before the new Woof process had
    a chance to write its discovery file. A second bridge could then acquire
    the lock, see "nothing running yet", and spawn a competing Woof instance
    — this reached production (two live Woof processes on different ports
    from one bridge race). The lock must stay held for the whole
    check-spawn-wait sequence, since only the lock holder is allowed to spawn.
    """
    fresh = discovery.DiscoveryInfo(pid=os.getpid(), port=9999, token="new")
    lock_was_held_during_wait = False

    async def fake_probe(info: discovery.DiscoveryInfo) -> bool:
        nonlocal lock_was_held_during_wait
        try:
            with discovery.startup_lock().acquire(timeout=0):
                pass  # lock was free — the bug this test guards against
        except discovery.LockTimeout:
            lock_was_held_during_wait = True
        return info == fresh

    def fake_spawn() -> None:
        discovery.write_discovery(fresh)

    with (
        patch("woof.bridge.probe_alive", side_effect=fake_probe),
        patch("woof.bridge._spawn_woof", side_effect=fake_spawn),
    ):
        result = await bridge.ensure_woof_running()

    assert result == fresh
    assert lock_was_held_during_wait is True


@pytest.mark.asyncio
async def test_ensure_woof_running_falls_back_to_waiting_when_lock_contended() -> None:
    """When another bridge already holds the lock, this bridge must only
    poll for that bridge's discovery file — never spawn a competing Woof.
    """
    fresh = discovery.DiscoveryInfo(pid=os.getpid(), port=9999, token="new")
    discovery.write_discovery(fresh)

    with (
        discovery.startup_lock().acquire(timeout=1.0),
        patch("woof.bridge._LOCK_TIMEOUT_SECONDS", 0.1),
        patch("woof.bridge.probe_alive", new=AsyncMock(return_value=True)),
        patch("woof.bridge._spawn_woof") as spawn,
    ):
        result = await bridge.ensure_woof_running()

    assert result == fresh
    spawn.assert_not_called()


def test_default_spawn_wait_seconds_has_generous_margin() -> None:
    # A too-tight margin here is exactly what caused the production crash
    # this fix addresses (Woof took ~16s to boot against a 15s budget).
    assert bridge._SPAWN_WAIT_SECONDS >= 30.0


def test_spawn_wait_seconds_overridable_via_env_var(monkeypatch) -> None:
    import importlib

    monkeypatch.setenv("WOOF_SPAWN_WAIT_SECONDS", "5")
    try:
        reloaded = importlib.reload(bridge)
        assert reloaded._SPAWN_WAIT_SECONDS == 5.0
    finally:
        monkeypatch.delenv("WOOF_SPAWN_WAIT_SECONDS", raising=False)
        importlib.reload(bridge)


def test_main_exits_nonzero_instead_of_crashing_when_woof_never_ready(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["woof-bridge"])
    with (
        patch("woof.bridge.setup_logging"),
        patch("woof.bridge.run_bridge", new=AsyncMock(side_effect=TimeoutError("boom"))),
        pytest.raises(SystemExit) as exc_info,
    ):
        bridge.main()
    assert exc_info.value.code == 1


def test_main_sets_filelock_logger_to_warning(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["woof-bridge", "--stop"])
    logging.getLogger("filelock").setLevel(logging.DEBUG)
    with (
        patch("woof.bridge.setup_logging"),
        patch("woof.bridge.stop_running_instance", new=AsyncMock(return_value=False)),
    ):
        bridge.main()
    assert logging.getLogger("filelock").level == logging.WARNING


def test_main_diagnose_flag_prints_state_without_running_bridge(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["woof-bridge", "--diagnose"])
    with (
        patch("woof.bridge.setup_logging"),
        patch("woof.bridge.run_bridge") as run_bridge_mock,
    ):
        bridge.main()
    run_bridge_mock.assert_not_called()
    captured = capsys.readouterr()
    assert "startup lock: free" in captured.out
    assert "discovery file: none" in captured.out


def test_diagnose_reports_live_discovery_pid(capsys) -> None:
    discovery.write_discovery(discovery.DiscoveryInfo(pid=os.getpid(), port=1234, token="tok"))
    bridge._print_lock_diagnosis()
    captured = capsys.readouterr()
    assert f"pid={os.getpid()}" in captured.out
    assert "NOT running" not in captured.out


@pytest.mark.asyncio
async def test_run_bridge_uses_generous_read_timeout_not_httpx_default() -> None:
    """Regression test: streamable_http_client() would normally build its own
    httpx client with generous MCP-appropriate timeouts (30s connect, 5min
    read), but passing our own client (needed so /keepalive can share it)
    opts out of that — silently falling back to httpx's bare 5s-everything
    default and dropping any tool call slower than 5s (e.g. a lazily-spawned
    Wally sidecar taking ~20s to come up). This reached production once
    already: `search_photos` timed out mid-Wally-spawn with no error, just a
    dropped SSE stream. The client we construct must override the timeout.
    """
    info = discovery.DiscoveryInfo(pid=os.getpid(), port=1234, token="tok")

    class _StopTest(Exception):
        pass

    def fake_async_client(*args, **kwargs) -> None:
        raise _StopTest(kwargs.get("timeout"))

    with (
        patch("woof.bridge.ensure_woof_running", new=AsyncMock(return_value=info)),
        patch("httpx.AsyncClient", side_effect=fake_async_client),
        pytest.raises(_StopTest) as exc_info,
    ):
        await bridge.run_bridge()

    timeout = exc_info.value.args[0]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect >= 30.0
    assert timeout.read >= 300.0


@pytest.mark.asyncio
async def test_stop_running_instance_returns_false_when_no_discovery_file() -> None:
    assert await bridge.stop_running_instance() is False


@pytest.mark.asyncio
async def test_stop_running_instance_cleans_up_stale_pid_without_http_call() -> None:
    discovery.write_discovery(discovery.DiscoveryInfo(pid=2**30, port=1, token="t"))
    with patch("httpx.AsyncClient.post") as post:
        result = await bridge.stop_running_instance()
    assert result is False
    post.assert_not_called()
    assert discovery.read_discovery() is None


@pytest.mark.asyncio
async def test_stop_running_instance_posts_shutdown_and_removes_file() -> None:
    info = discovery.DiscoveryInfo(pid=os.getpid(), port=1234, token="tok")
    discovery.write_discovery(info)
    mock_response = AsyncMock()
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as post:
        result = await bridge.stop_running_instance()
    assert result is True
    post.assert_called_once()
    called_url, called_kwargs = post.call_args[0], post.call_args[1]
    assert called_url[0].endswith("/shutdown")
    assert called_kwargs["headers"]["Authorization"] == "Bearer tok"
    assert discovery.read_discovery() is None
