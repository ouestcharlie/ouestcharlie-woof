"""Regression test for the Windows-specific bug fixed in discovery.is_pid_alive.

os.kill(pid, 0) is not a safe existence check on Windows: signal 0 is
CTRL_C_EVENT, so it calls GenerateConsoleCtrlEvent, which fails with
ERROR_INVALID_PARAMETER whenever the *caller* has no console of its own —
exactly how woof-bridge runs when launched by a GUI MCP host (Claude
Desktop, VS Code, etc., as opposed to a terminal). That made is_pid_alive()
always report "dead" for a genuinely live Woof process, so the bridge spun
for the full startup timeout (_SPAWN_WAIT_SECONDS) instead of finding it.

Only meaningful on win32 — os.kill(pid, 0) is already a correct existence
check on POSIX, covered by tests/test_discovery.py.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only console/pid bug")

_PROBER_SCRIPT = Path(__file__).parent / "pid_alive_prober.py"


def _run_prober_without_console(target_pid: int, out_path: Path) -> str:
    proc = subprocess.Popen(
        [sys.executable, str(_PROBER_SCRIPT), str(target_pid), str(out_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS,  # no console at all -- the crux of the bug
    )
    proc.wait(timeout=10)
    return out_path.read_text(encoding="utf-8").strip()


def test_is_pid_alive_true_for_live_process_from_console_less_caller(tmp_path: Path) -> None:
    target = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        time.sleep(0.5)
        result = _run_prober_without_console(target.pid, tmp_path / "result.txt")
        assert result == "True"
    finally:
        target.kill()
        target.wait(timeout=5)


def test_is_pid_alive_false_for_exited_process_from_console_less_caller(tmp_path: Path) -> None:
    target = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    target.wait(timeout=5)

    result = _run_prober_without_console(target.pid, tmp_path / "result.txt")

    assert result == "False"
