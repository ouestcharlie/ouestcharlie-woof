"""Logging setup for Woof.

Provides a platform-aware rotating file logger that keeps stdout clean
for MCP stdio transport by redirecting stderr to the log file.

Platform log directories (namespace "ouestcharlie"):
- macOS/iOS: ~/Library/Logs/ouestcharlie/
- Android:   ~/logs/ouestcharlie/  (app sandbox home, set by the runtime)
- Linux:     $XDG_STATE_HOME/ouestcharlie/  (default: ~/.local/state/ouestcharlie/)
- Windows:   %LOCALAPPDATA%\\ouestcharlie\\logs\\
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _default_log_dir() -> Path:
    # sys.platform == "android" is set by Python 3.13+ on Android (PEP 738).
    if sys.platform == "android":
        # App sandbox home is set by the Android runtime; no XDG conventions apply.
        return Path.home() / "logs" / "ouestcharlie"
    system = platform.system()
    if system == "Darwin":
        # Covers both macOS and iOS (same Darwin kernel, same sandbox layout).
        return Path.home() / "Library" / "Logs" / "ouestcharlie"
    elif system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "ouestcharlie" / "logs"
    else:
        xdg = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        return Path(xdg) / "ouestcharlie"


def setup_logging(
    agent_name: str,
    *,
    log_file_env_var: str | None = None,
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
    redirect_stderr: bool = True,
) -> Path:
    """Configure rotating file logging for an MCP server.

    Writes all log output (and optionally stderr) to a file so that
    stdout stays clean for MCP stdio transport.

    Args:
        agent_name: Used for the default log file name
                    (e.g. "woof" → ~/Library/Logs/ouestcharlie/woof.log).
        log_file_env_var: If set, the named environment variable overrides the
                          default log file path (e.g. "WOOF_LOG_FILE").
        level: Root logger level (default: INFO).
        max_bytes: Maximum size of a single log file before rotation.
        backup_count: Number of rotated backup files to keep.
        redirect_stderr: When True, sys.stderr is redirected to the log file so
                         that stray writes from third-party libraries don't reach
                         MCP stdio. Defaults to True.

    Returns:
        Path of the log file that was opened.
    """
    override = os.environ.get(log_file_env_var) if log_file_env_var else None

    log_file = Path(override) if override else _default_log_dir() / f"{agent_name}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    if redirect_stderr:
        sys.stderr = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115

    return log_file
