"""Stand-in Woof process for bridge/discovery integration tests.

Deliberately does NOT import woof.mcp_server / agent_client — it only needs
to satisfy the *process-lifecycle* contract woof.bridge depends on: bind a
real loopback port, write a real discovery file, and serve /healthz and
/shutdown behind the shared bearer token. Standing in for the full Woof app
keeps these tests fast (no Wally/Whitebeard startup) while still exercising
bridge.py's spawn/discover/probe path against a genuine separate OS process
rather than an in-process mock.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from woof import discovery


def main() -> None:
    print("fake_woof: starting", flush=True)
    app_dir, token = sys.argv[1], sys.argv[2]
    discovery._APP_DIR = Path(app_dir)  # noqa: SLF001
    discovery._DISCOVERY_FILE = discovery._APP_DIR / "woof-discovery.json"  # noqa: SLF001
    discovery._LOCK_FILE = discovery._APP_DIR / "woof-discovery.lock"  # noqa: SLF001
    print(f"fake_woof: app_dir={discovery._APP_DIR}", flush=True)  # noqa: SLF001

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass  # keep test output clean

        def _authorized(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {token}"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz" and self._authorized():
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/shutdown" and self._authorized():
                self.send_response(200)
                self.end_headers()
                discovery.remove_discovery()
                self.server.shutdown()
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"fake_woof: bound port={server.server_address[1]}", flush=True)
    discovery.write_discovery(
        discovery.DiscoveryInfo(pid=os.getpid(), port=server.server_address[1], token=token)
    )
    print(f"fake_woof: wrote discovery file at {discovery._DISCOVERY_FILE}", flush=True)  # noqa: SLF001
    server.serve_forever()


if __name__ == "__main__":
    main()
