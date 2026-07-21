"""Tests for woof.bridge: lazy-start coordination and --stop, mocking discovery I/O
and process spawning so no real Woof instance is ever started."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

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
