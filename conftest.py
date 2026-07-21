"""Root conftest: make tests/-only helper modules importable from tests_integration/ too."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tests"))
