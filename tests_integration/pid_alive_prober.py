"""Helper for test_pid_alive_windows.py.

Runs is_pid_alive() from inside a process that itself has no console
(matching how a GUI-launched woof-bridge runs) and reports the result to a
file, since a console-less process has no stdout a parent can capture
normally.
"""

from __future__ import annotations

import sys

from woof.discovery import is_pid_alive


def main() -> None:
    target_pid, out_path = int(sys.argv[1]), sys.argv[2]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(str(is_pid_alive(target_pid)))


if __name__ == "__main__":
    main()
