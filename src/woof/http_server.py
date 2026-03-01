"""Local HTTP server for serving thumbnail and preview AVIF containers.

Runs on 127.0.0.1 with an OS-assigned port in a daemon thread.  The gallery
iframe fetches images directly from this server; photo data never passes
through the MCP channel.

URL scheme:
  GET /thumbnails/{backend_name}/{partition}/thumbnails.avif
  GET /previews/{backend_name}/{partition}/previews.avif

where {partition} may contain slashes (e.g. "2024/2024-07").
The corresponding file on disk is:
  {backend.path}/{partition}/.ouestcharlie/thumbnails.avif
  {backend.path}/{partition}/.ouestcharlie/previews.avif
"""

from __future__ import annotations

import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

if TYPE_CHECKING:
    from .config import WoofConfig

_log = logging.getLogger(__name__)

# File extension → AVIF filename inside .ouestcharlie/
_SUFFIX_MAP = {
    "thumbnails.avif": "thumbnails.avif",
    "previews.avif": "previews.avif",
}


def start_http_server(config: "WoofConfig") -> int:
    """Start the thumbnail HTTP server in a daemon thread.

    Returns:
        The port number the server is listening on.
    """
    port_queue: queue.Queue[int] = queue.Queue()

    def _run() -> None:
        def handler_factory(*args: object, **kwargs: object) -> _ThumbnailHandler:
            return _ThumbnailHandler(config, *args, **kwargs)  # type: ignore[arg-type]

        server = HTTPServer(("127.0.0.1", 0), handler_factory)
        port_queue.put(server.server_address[1])
        _log.info("Thumbnail HTTP server listening on 127.0.0.1:%d", server.server_address[1])
        server.serve_forever()

    thread = threading.Thread(target=_run, daemon=True, name="woof-http")
    thread.start()
    return port_queue.get(timeout=10)


class _ThumbnailHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves AVIF files from the local backend."""

    def __init__(self, config: "WoofConfig", *args: object, **kwargs: object) -> None:
        self._config = config
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.lstrip("/"))
        # path = "{kind}/{backend_name}/{partition}/{filename}"
        # kind is "thumbnails" or "previews"
        parts = path.split("/", 2)
        if len(parts) < 3:
            self._not_found()
            return

        _kind, backend_name, rest = parts
        # rest = "{partition}/{filename}" — filename is the last segment
        rest_parts = rest.rsplit("/", 1)
        if len(rest_parts) != 2:
            self._not_found()
            return

        partition, filename = rest_parts
        if filename not in _SUFFIX_MAP:
            self._not_found()
            return

        backend = self._config.get_backend(backend_name)
        if backend is None:
            _log.warning("Unknown backend %r in HTTP request", backend_name)
            self._not_found()
            return

        file_path = Path(backend.path) / partition / ".ouestcharlie" / _SUFFIX_MAP[filename]
        if not file_path.exists():
            _log.debug("File not found: %s", file_path)
            self._not_found()
            return

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/avif")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _not_found(self) -> None:
        self.send_error(404)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        _log.debug("HTTP %s", fmt % args)
