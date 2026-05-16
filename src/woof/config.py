"""Woof configuration — libraries stored in ~/.ouestcharlie/config.json."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

_log = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = Path(user_config_dir("ouestcharlie"))


@dataclass
class LibraryConfig:
    """A registered photo library."""

    name: str
    """User-chosen label, unique within the config."""
    type: str
    """Storage type: "filesystem" for a local folder, "cloud_mount" for a FUSE/CF-API mount."""
    path: str
    """Absolute path to the photo root directory."""

    def to_agent_env(self) -> dict[str, str]:
        """Serialise to the dict expected by WOOF_BACKEND_CONFIG."""
        return {"name": self.name, "type": self.type, "root": self.path}


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
