"""Tests for WoofConfig — load, save, backend management."""

from __future__ import annotations

from pathlib import Path

import pytest

from woof.config import BackendConfig, WoofConfig


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / "woof"


# ------------------------------------------------------------------
# load()
# ------------------------------------------------------------------

def test_load_missing_file(config_dir: Path) -> None:
    config = WoofConfig.load(config_dir=config_dir)
    assert config.backends == []
    assert config.config_dir == config_dir


def test_save_and_reload(config_dir: Path) -> None:
    WoofConfig(
        backends=[BackendConfig(name="test", type="local", path="/photos")],
        config_dir=config_dir,
    ).save()

    loaded = WoofConfig.load(config_dir=config_dir)
    assert len(loaded.backends) == 1
    assert loaded.backends[0].name == "test"
    assert loaded.backends[0].path == "/photos"


def test_load_invalid_json(config_dir: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text("not json")
    config = WoofConfig.load(config_dir=config_dir)  # should not raise
    assert config.backends == []


# ------------------------------------------------------------------
# save()
# ------------------------------------------------------------------

def test_save_creates_directory(config_dir: Path) -> None:
    assert not config_dir.exists()
    WoofConfig(
        backends=[BackendConfig(name="x", type="local", path="/p")],
        config_dir=config_dir,
    ).save()
    assert (config_dir / "config.json").exists()


# ------------------------------------------------------------------
# add_backend()
# ------------------------------------------------------------------

def test_add_backend_persists(config_dir: Path) -> None:
    config = WoofConfig.load(config_dir=config_dir)
    config.add_backend(BackendConfig(name="mylib", type="local", path="/pics"))

    reloaded = WoofConfig.load(config_dir=config_dir)
    assert reloaded.get_backend("mylib") is not None
    assert reloaded.get_backend("mylib").path == "/pics"  # type: ignore[union-attr]


def test_add_backend_replaces_existing(config_dir: Path) -> None:
    config = WoofConfig.load(config_dir=config_dir)
    config.add_backend(BackendConfig(name="lib", type="local", path="/old"))
    config.add_backend(BackendConfig(name="lib", type="local", path="/new"))

    assert len(config.backends) == 1
    assert config.backends[0].path == "/new"


# ------------------------------------------------------------------
# get_backend()
# ------------------------------------------------------------------

def test_get_backend_missing_returns_none() -> None:
    config = WoofConfig()
    assert config.get_backend("nonexistent") is None


# ------------------------------------------------------------------
# BackendConfig
# ------------------------------------------------------------------

def test_to_agent_env() -> None:
    b = BackendConfig(name="x", type="local", path="/mnt/photos")
    assert b.to_agent_env() == {"type": "filesystem", "root": "/mnt/photos"}


# ------------------------------------------------------------------
# default config_dir
# ------------------------------------------------------------------

def test_default_config_dir_is_platform_specific() -> None:
    from platformdirs import user_config_dir
    cfg = WoofConfig()
    assert cfg.config_dir == Path(user_config_dir("ouestcharlie"))
