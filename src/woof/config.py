"""Woof configuration — backends stored in ~/.ouestcharlie/config.json."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

_log = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = Path(user_config_dir("ouestcharlie"))


@dataclass
class BackendConfig:
    """A registered photo library backend."""

    name: str
    """User-chosen label, unique within the config."""
    type: str
    """Storage type. Always 'local' for V1."""
    path: str
    """Absolute path to the photo root directory."""

    def to_agent_env(self) -> dict[str, str]:
        """Serialise to the dict expected by WOOF_BACKEND_CONFIG."""
        return {"type": "filesystem", "root": self.path}


@dataclass
class WoofConfig:
    """Device-local Woof configuration."""

    backends: list[BackendConfig] = field(default_factory=list)
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
            backends = [BackendConfig(**b) for b in raw.get("backends", [])]
            return cls(backends=backends, config_dir=config_dir)
        except Exception as exc:
            _log.warning("Failed to parse config %s: %s — starting empty", config_file, exc)
            return cls(config_dir=config_dir)

    def save(self) -> None:
        """Persist config to disk."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        config_file = self.config_dir / "config.json"
        data: dict = {"backends": [asdict(b) for b in self.backends]}
        config_file.write_text(json.dumps(data, indent=2))
        _log.debug("Config saved to %s", config_file)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_backend(self, name: str) -> BackendConfig | None:
        """Return the backend with the given name, or None."""
        for b in self.backends:
            if b.name == name:
                return b
        return None

    def add_backend(self, backend: BackendConfig) -> None:
        """Add or replace a backend by name, then persist."""
        self.backends = [b for b in self.backends if b.name != backend.name]
        self.backends.append(backend)
        self.save()
