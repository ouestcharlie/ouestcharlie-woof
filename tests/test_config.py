"""Tests for WoofConfig — load, save, backend management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from woof.config import BackendConfig, WoofConfig


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.ouestcharlie to a temp directory."""
    import woof.config as _mod
    monkeypatch.setattr(_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(_mod, "_CONFIG_FILE", tmp_path / "config.json")
    return tmp_path


def test_load_missing_file(config_dir: Path) -> None:
    config = WoofConfig.load()
    assert config.backends == []


def test_save_and_reload(config_dir: Path) -> None:
    config = WoofConfig(backends=[BackendConfig(name="test", type="local", path="/photos")])
    config.save()

    loaded = WoofConfig.load()
    assert len(loaded.backends) == 1
    assert loaded.backends[0].name == "test"
    assert loaded.backends[0].path == "/photos"


def test_add_backend_persists(config_dir: Path) -> None:
    config = WoofConfig.load()
    config.add_backend(BackendConfig(name="mylib", type="local", path="/pics"))

    reloaded = WoofConfig.load()
    assert reloaded.get_backend("mylib") is not None
    assert reloaded.get_backend("mylib").path == "/pics"  # type: ignore[union-attr]


def test_add_backend_replaces_existing(config_dir: Path) -> None:
    config = WoofConfig.load()
    config.add_backend(BackendConfig(name="lib", type="local", path="/old"))
    config.add_backend(BackendConfig(name="lib", type="local", path="/new"))

    assert len(config.backends) == 1
    assert config.backends[0].path == "/new"


def test_get_backend_missing_returns_none(config_dir: Path) -> None:
    config = WoofConfig.load()
    assert config.get_backend("nonexistent") is None


def test_load_invalid_json(config_dir: Path) -> None:
    (config_dir / "config.json").write_text("not json")
    config = WoofConfig.load()  # should not raise
    assert config.backends == []


def test_to_agent_env() -> None:
    b = BackendConfig(name="x", type="local", path="/mnt/photos")
    env = b.to_agent_env()
    assert env == {"type": "filesystem", "root": "/mnt/photos"}


def test_save_creates_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nested = tmp_path / "a" / "b" / ".ouestcharlie"
    import woof.config as _mod
    monkeypatch.setattr(_mod, "_CONFIG_DIR", nested)
    monkeypatch.setattr(_mod, "_CONFIG_FILE", nested / "config.json")

    config = WoofConfig(backends=[BackendConfig(name="x", type="local", path="/p")])
    config.save()
    assert (nested / "config.json").exists()
