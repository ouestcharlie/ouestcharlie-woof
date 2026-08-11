"""Woof configuration — libraries stored in ~/.ouestcharlie/config.json."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

_log = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = Path(user_config_dir("ouestcharlie"))


def _resolve_to_unc(path: str) -> str | None:
    """On Windows, return the UNC path if *path* is or resolves to a UNC share; else None.

    Handles both explicit UNC paths (\\\\server\\share\\...) and mapped drive
    letters (Z:\\...) backed by a network share.
    Uses Path.resolve() — same pattern as LocalBackend — to expand drive letters
    to their real UNC target before checking.
    """
    if sys.platform != "win32":
        return None
    _log.debug("_resolve_to_unc: incoming path %r", path)
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    _log.debug("_resolve_to_unc: resolved to %r", resolved)
    if resolved.anchor.startswith("\\\\"):
        return str(resolved)
    # Mapped drive: ask Windows for the universal name via WNetGetUniversalNameW
    import ctypes

    UNIVERSAL_NAME_INFO_LEVEL = 1
    DRIVE_REMOTE = 4
    drive_root = resolved.anchor  # e.g. "Z:\\"
    if not drive_root:
        return None
    drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_root)
    _log.debug("_resolve_to_unc: drive %r has type %d (REMOTE=4)", drive_root, drive_type)
    if drive_type != DRIVE_REMOTE:
        return None
    buf = ctypes.create_unicode_buffer(1024)
    buf_size = ctypes.c_ulong(ctypes.sizeof(buf))
    if (
        ctypes.windll.mpr.WNetGetUniversalNameW(
            drive_root, UNIVERSAL_NAME_INFO_LEVEL, buf, ctypes.byref(buf_size)
        )
        != 0
    ):
        return None

    class _UniversalNameInfo(ctypes.Structure):
        _fields_ = [("lpUniversalName", ctypes.c_wchar_p)]

    unc_root = _UniversalNameInfo.from_buffer(buf).lpUniversalName
    rel = str(resolved)[len(drive_root) :]
    return f"{unc_root}\\{rel}" if rel else unc_root


def get_local_lance_index_path(library_name: str) -> str | None:
    """Return a local NTFS index path for *library_name* on Windows, else None.

    Used when the library root is a UNC path where object_store is unreliable.
    Falls back to ~/AppData/Local if LOCALAPPDATA is unset.
    """
    if sys.platform != "win32":
        return None
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in library_name)
    return str(Path(local_app_data) / "ouestcharlie" / "indexes" / safe_name)


@dataclass
class LibraryConfig:
    """A registered photo library."""

    name: str
    """User-chosen label, unique within the config."""
    type: str
    """Storage type: "filesystem" for a local folder, "cloud_mount" for a FUSE/CF-API mount."""
    path: str
    """Absolute path to the photo root directory."""
    lancedb_index_path: str | None = None
    """Optional override for the LanceDB index location."""

    @classmethod
    def create(cls, name: str, path: str, library_type: str = "filesystem") -> LibraryConfig:
        """Create a LibraryConfig, auto-detecting a local LanceDB path for UNC roots.

        On Windows, when *path* resolves to a UNC share (explicit ``\\\\server\\share``
        or a mapped drive letter), ``lancedb_index_path`` is set to a local NTFS
        location so object_store can operate reliably.
        """
        lance_path = get_local_lance_index_path(name) if _resolve_to_unc(path) is not None else None
        return cls(name=name, type=library_type, path=path, lancedb_index_path=lance_path)

    def to_dict(self) -> dict[str, str]:
        """Serialize to a plain dict of all non-None fields."""
        result: dict[str, str] = {"name": self.name, "path": self.path, "type": self.type}
        if self.lancedb_index_path is not None:
            result["lancedb_index_path"] = self.lancedb_index_path
        return result


@dataclass
class WoofConfig:
    """Device-local Woof configuration."""

    libraries: list[LibraryConfig] = field(default_factory=list)
    config_dir: Path = field(default_factory=lambda: _DEFAULT_CONFIG_DIR)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, config_dir: Path | None = None) -> WoofConfig:
        """Load config from disk, creating an empty one if absent."""
        if config_dir is None:
            config_dir = _DEFAULT_CONFIG_DIR
        config_file = config_dir / "config.json"
        if not config_file.exists():
            _log.info("No config found at %s — starting empty", config_file)
            return cls(config_dir=config_dir)
        try:
            raw = json.loads(config_file.read_text())
            key_migrated = "backends" in raw and "libraries" not in raw
            library_data = raw.get("libraries") or raw.get("backends", [])
            libraries = [LibraryConfig(**b) for b in library_data]
            config = cls(libraries=libraries, config_dir=config_dir)
            type_migrated = config._migrate()
            if key_migrated or type_migrated:
                config.save()
            return config
        except Exception as exc:
            _log.warning("Failed to parse config %s: %s — starting empty", config_file, exc)
            return cls(config_dir=config_dir)

    def save(self) -> None:
        """Persist config to disk."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        config_file = self.config_dir / "config.json"
        data: dict = {"libraries": [asdict(b) for b in self.libraries]}
        config_file.write_text(json.dumps(data, indent=2))
        _log.debug("Config saved to %s", config_file)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def _migrate(self) -> bool:
        """Upgrade legacy stored values in-place and persist if anything changed."""
        migrated = False
        for b in self.libraries:
            if b.type == "local":
                _log.info("Migrating library %r type 'local' → 'filesystem'", b.name)
                b.type = "filesystem"
                migrated = True
            if b.lancedb_index_path is None and _resolve_to_unc(b.path) is not None:
                b.lancedb_index_path = get_local_lance_index_path(b.name)
                if b.lancedb_index_path:
                    _log.info(
                        "Migrating library %r: setting lancedb_index_path to %r",
                        b.name,
                        b.lancedb_index_path,
                    )
                    migrated = True
        return migrated

    def get_library(self, name: str) -> LibraryConfig | None:
        """Return the library with the given name, or None."""
        for b in self.libraries:
            if b.name == name:
                return b
        return None

    def add_library(self, library: LibraryConfig) -> None:
        """Add or replace a library by name, then persist."""
        self.libraries = [b for b in self.libraries if b.name != library.name]
        self.libraries.append(library)
        self.save()

    def remove_library(self, name: str) -> None:
        """Remove a library by name, then persist. Raise KeyError if absent."""
        if not any(b.name == name for b in self.libraries):
            raise KeyError(name)
        self.libraries = [b for b in self.libraries if b.name != name]
        self.save()
