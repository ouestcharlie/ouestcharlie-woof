"""Tests for WoofConfig — load, save, library management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from woof.config import LibraryConfig, WoofConfig, _resolve_to_unc


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / "woof"


# ------------------------------------------------------------------
# load()
# ------------------------------------------------------------------


def test_load_missing_file(config_dir: Path) -> None:
    config = WoofConfig.load(config_dir=config_dir)
    assert config.libraries == []
    assert config.config_dir == config_dir


def test_save_and_reload(config_dir: Path) -> None:
    WoofConfig(
        libraries=[LibraryConfig(name="test", type="filesystem", path="/photos")],
        config_dir=config_dir,
    ).save()

    loaded = WoofConfig.load(config_dir=config_dir)
    assert len(loaded.libraries) == 1
    assert loaded.libraries[0].name == "test"
    assert loaded.libraries[0].type == "filesystem"
    assert loaded.libraries[0].path == "/photos"


def test_load_migrates_local_type_to_filesystem(config_dir: Path) -> None:
    """Legacy 'local' type stored in config.json is upgraded to 'filesystem' on load."""
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        '{"libraries": [{"name": "lib", "type": "local", "path": "/photos"}]}'
    )

    loaded = WoofConfig.load(config_dir=config_dir)

    assert loaded.libraries[0].type == "filesystem"
    # Migration must be persisted so the next load also sees the correct type.
    raw = (config_dir / "config.json").read_text()
    assert '"local"' not in raw
    assert '"filesystem"' in raw


def test_load_migrates_backends_key_to_libraries(config_dir: Path) -> None:
    """Old config.json with 'backends' key is rewritten to 'libraries' on load."""
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        '{"backends": [{"name": "lib", "type": "filesystem", "path": "/photos"}]}'
    )

    loaded = WoofConfig.load(config_dir=config_dir)

    assert len(loaded.libraries) == 1
    assert loaded.libraries[0].name == "lib"
    raw = (config_dir / "config.json").read_text()
    assert '"backends"' not in raw
    assert '"libraries"' in raw


def test_load_migrates_backends_key_and_local_type_together(config_dir: Path) -> None:
    """Both key migration and type migration run correctly on a legacy config."""
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        '{"backends": [{"name": "lib", "type": "local", "path": "/photos"}]}'
    )

    loaded = WoofConfig.load(config_dir=config_dir)

    assert loaded.libraries[0].type == "filesystem"
    raw = (config_dir / "config.json").read_text()
    assert '"backends"' not in raw
    assert '"local"' not in raw
    assert '"libraries"' in raw
    assert '"filesystem"' in raw


def test_load_invalid_json(config_dir: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text("not json")
    config = WoofConfig.load(config_dir=config_dir)  # should not raise
    assert config.libraries == []


# ------------------------------------------------------------------
# save()
# ------------------------------------------------------------------


def test_save_creates_directory(config_dir: Path) -> None:
    assert not config_dir.exists()
    WoofConfig(
        libraries=[LibraryConfig(name="x", type="local", path="/p")],
        config_dir=config_dir,
    ).save()
    assert (config_dir / "config.json").exists()


# ------------------------------------------------------------------
# add_library()
# ------------------------------------------------------------------


def test_add_library_persists(config_dir: Path) -> None:
    config = WoofConfig.load(config_dir=config_dir)
    config.add_library(LibraryConfig(name="mylib", type="local", path="/pics"))

    reloaded = WoofConfig.load(config_dir=config_dir)
    assert reloaded.get_library("mylib") is not None
    assert reloaded.get_library("mylib").path == "/pics"  # type: ignore[union-attr]


def test_add_library_replaces_existing(config_dir: Path) -> None:
    config = WoofConfig.load(config_dir=config_dir)
    config.add_library(LibraryConfig(name="lib", type="local", path="/old"))
    config.add_library(LibraryConfig(name="lib", type="local", path="/new"))

    assert len(config.libraries) == 1
    assert config.libraries[0].path == "/new"


# ------------------------------------------------------------------
# get_library()
# ------------------------------------------------------------------


def test_get_library_missing_returns_none() -> None:
    config = WoofConfig()
    assert config.get_library("nonexistent") is None


# ------------------------------------------------------------------
# LibraryConfig
# ------------------------------------------------------------------


def test_to_dict() -> None:
    b = LibraryConfig(name="x", type="filesystem", path="/mnt/photos")
    assert b.to_dict() == {"name": "x", "type": "filesystem", "path": "/mnt/photos"}


def test_to_dict_cloud_mount() -> None:
    b = LibraryConfig(name="kdrive", type="cloud_mount", path="/mnt/kdrive")
    assert b.to_dict() == {"name": "kdrive", "type": "cloud_mount", "path": "/mnt/kdrive"}


# ------------------------------------------------------------------
# default config_dir
# ------------------------------------------------------------------


def test_default_config_dir_is_platform_specific() -> None:
    from platformdirs import user_config_dir

    cfg = WoofConfig()
    assert cfg.config_dir == Path(user_config_dir("ouestcharlie"))


def test_to_dict_includes_lancedb_index_path() -> None:
    b = LibraryConfig(
        name="nas", type="filesystem", path="/mnt/nas", lancedb_index_path="/local/index"
    )
    assert b.to_dict()["lancedb_index_path"] == "/local/index"


def test_to_dict_omits_lancedb_index_path_when_none() -> None:
    b = LibraryConfig(name="x", type="filesystem", path="/photos")
    assert "lancedb_index_path" not in b.to_dict()


# ------------------------------------------------------------------
# _resolve_to_unc
# ------------------------------------------------------------------


def test_resolve_to_unc_non_windows_returns_none() -> None:
    with patch("woof.config.sys") as mock_sys:
        mock_sys.platform = "darwin"
        result = _resolve_to_unc("/some/local/path")
    assert result is None


def test_resolve_to_unc_explicit_unc_path() -> None:
    unc = r"\\server\share\photos"
    with (
        patch("woof.config.sys") as mock_sys,
        patch("woof.config.Path") as mock_path_cls,
    ):
        mock_sys.platform = "win32"
        resolved = MagicMock()
        resolved.anchor = r"\\server\share\\"
        resolved.__str__ = lambda self: unc
        mock_path_cls.return_value.resolve.return_value = resolved
        result = _resolve_to_unc(unc)
    assert result == unc


def test_resolve_to_unc_local_drive_returns_none() -> None:
    mock_ctypes = MagicMock()
    mock_ctypes.windll.kernel32.GetDriveTypeW.return_value = 3  # DRIVE_FIXED
    with (
        patch("woof.config.sys") as mock_sys,
        patch("woof.config.Path") as mock_path_cls,
        patch.dict("sys.modules", {"ctypes": mock_ctypes}),
    ):
        mock_sys.platform = "win32"
        resolved = MagicMock()
        resolved.anchor = "C:\\"
        mock_path_cls.return_value.resolve.return_value = resolved
        result = _resolve_to_unc("C:\\photos")
    assert result is None


def test_resolve_to_unc_mapped_drive_wnet_failure_returns_none() -> None:
    """When WNetGetUniversalNameW fails (non-zero return), result is None."""
    mock_ctypes = MagicMock()
    mock_ctypes.windll.kernel32.GetDriveTypeW.return_value = 4  # DRIVE_REMOTE
    mock_ctypes.windll.mpr.WNetGetUniversalNameW.return_value = 1  # ERROR
    with (
        patch("woof.config.sys") as mock_sys,
        patch("woof.config.Path") as mock_path_cls,
        patch.dict("sys.modules", {"ctypes": mock_ctypes}),
    ):
        mock_sys.platform = "win32"
        resolved = MagicMock()
        resolved.anchor = "Z:\\"
        mock_path_cls.return_value.resolve.return_value = resolved
        result = _resolve_to_unc("Z:\\photos")
    assert result is None
