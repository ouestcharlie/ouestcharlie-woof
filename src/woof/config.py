"""Woof configuration — backends stored in ~/.ouestcharlie/config.json."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".ouestcharlie"
_CONFIG_FILE = _CONFIG_DIR / "config.json"


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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls) -> "WoofConfig":
        """Load config from disk, creating an empty one if absent."""
        if not _CONFIG_FILE.exists():
            _log.info("No config found at %s — starting empty", _CONFIG_FILE)
            return cls()
        try:
            raw = json.loads(_CONFIG_FILE.read_text())
            backends = [BackendConfig(**b) for b in raw.get("backends", [])]
            return cls(backends=backends)
        except Exception as exc:
            _log.warning("Failed to parse config %s: %s — starting empty", _CONFIG_FILE, exc)
            return cls()

    def save(self) -> None:
        """Persist config to disk."""
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {"backends": [asdict(b) for b in self.backends]}
        _CONFIG_FILE.write_text(json.dumps(data, indent=2))
        _log.debug("Config saved to %s", _CONFIG_FILE)

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
