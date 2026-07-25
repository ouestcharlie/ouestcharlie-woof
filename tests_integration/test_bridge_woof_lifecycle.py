"""Integration tests for woof-bridge's handling of the Woof process lifecycle:
spawn, discovery, liveness probing, reuse, and stop — against a real, separate
OS process (see fake_woof.py) rather than the mocked-out I/O used in
tests/test_bridge.py. Nothing here mocks probe_alive, is_pid_alive, or
discovery file I/O, so a regression in any of those would fail these tests
even though it wouldn't show up in the mocked unit tests.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from woof import bridge, discovery

_FAKE_WOOF_SCRIPT = Path(__file__).parent / "fake_woof.py"
_TOKEN = "integration-test-token"  # noqa: S105


@pytest.fixture(autouse=True)
def _isolated_discovery_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(discovery, "_APP_DIR", tmp_path)
    monkeypatch.setattr(discovery, "_DISCOVERY_FILE", tmp_path / "woof-discovery.json")
    monkeypatch.setattr(discovery, "_LOCK_FILE", tmp_path / "woof-discovery.lock")
    yield


@pytest.fixture()
def spawn_fake_woof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace bridge._spawn_woof with one that launches the real fake-woof
    subprocess, using the same detached/new-process-group flags as
    production's _spawn_woof. Force-kills anything it spawned once the test
    ends, even on failure, so a bug can't leak an orphaned process."""
    spawned: list[subprocess.Popen] = []

    def _spawn() -> None:
        kwargs: dict[str, object] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [sys.executable, str(_FAKE_WOOF_SCRIPT), str(tmp_path), _TOKEN],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        spawned.append(proc)

    spy = Mock(side_effect=_spawn)
    monkeypatch.setattr(bridge, "_spawn_woof", spy)
    yield spy

    for proc in spawned:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_ensure_woof_running_spawns_and_discovers_real_process(
    spawn_fake_woof: Mock,
) -> None:
    info = await bridge.ensure_woof_running()

    assert info.token == _TOKEN
    assert discovery.is_pid_alive(info.pid)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{info.server_url}/healthz",
            headers={"Authorization": f"Bearer {info.token}"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ensure_woof_running_reuses_live_instance_without_respawning(
    spawn_fake_woof: Mock,
) -> None:
    first = await bridge.ensure_woof_running()
    second = await bridge.ensure_woof_running()

    assert second == first
    spawn_fake_woof.assert_called_once()


@pytest.mark.asyncio
async def test_stop_running_instance_terminates_real_process(spawn_fake_woof: Mock) -> None:
    info = await bridge.ensure_woof_running()

    result = await bridge.stop_running_instance()

    assert result is True
    assert discovery.read_discovery() is None
    assert await _wait_until(lambda: not discovery.is_pid_alive(info.pid))

    with pytest.raises(httpx.HTTPError):
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.get(f"{info.server_url}/healthz")
