"""Tests for the thumbnail HTTP server."""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from woof.config import BackendConfig, WoofConfig
from woof.http_server import start_http_server


@pytest.fixture()
def backend_root(tmp_path: Path) -> Path:
    """A fake photo root with one partition containing AVIF placeholders."""
    partition = tmp_path / "2024" / "2024-07" / ".ouestcharlie"
    partition.mkdir(parents=True)
    (partition / "thumbnails.avif").write_bytes(b"AVIF_FAKE_THUMB")
    (partition / "previews.avif").write_bytes(b"AVIF_FAKE_PREVIEW")
    return tmp_path


@pytest.fixture()
def config(backend_root: Path) -> WoofConfig:
    return WoofConfig(
        backends=[BackendConfig(name="testlib", type="local", path=str(backend_root))]
    )


def test_thumbnail_served(config: WoofConfig) -> None:
    port = start_http_server(config)
    url = f"http://127.0.0.1:{port}/thumbnails/testlib/2024/2024-07/thumbnails.avif"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        assert resp.read() == b"AVIF_FAKE_THUMB"


def test_preview_served(config: WoofConfig) -> None:
    port = start_http_server(config)
    url = f"http://127.0.0.1:{port}/previews/testlib/2024/2024-07/previews.avif"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        assert resp.read() == b"AVIF_FAKE_PREVIEW"


def test_unknown_backend_returns_404(config: WoofConfig) -> None:
    port = start_http_server(config)
    url = f"http://127.0.0.1:{port}/thumbnails/unknown/2024/2024-07/thumbnails.avif"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 404


def test_missing_file_returns_404(config: WoofConfig) -> None:
    port = start_http_server(config)
    url = f"http://127.0.0.1:{port}/thumbnails/testlib/2024/2024-08/thumbnails.avif"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(url)
    assert exc_info.value.code == 404


def test_gallery_route_serves_html(config: WoofConfig) -> None:
    port = start_http_server(config)
    url = f"http://127.0.0.1:{port}/gallery?backend=testlib"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        body = resp.read().decode()
        assert "<html" in body
