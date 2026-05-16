"""Shared fixtures for Woof integration tests.

Integration tests spawn real subprocesses (Whitebeard, Wally) and bind real
network ports.  They are slower than unit tests and are kept in this separate
directory so they can be run selectively:

    pytest tests_integration/ -v

Requirements: the ouestcharlie-whitebeard and ouestcharlie-wally packages must
be installed in the same Python environment as Woof (already the case when
working from the repo with uv).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from woof.agent_client import AgentClient
from woof.config import LibraryConfig, WoofConfig


@pytest.fixture()
def library(tmp_path: Path) -> LibraryConfig:
    """A minimal backend config pointing at an empty temporary directory."""
    return LibraryConfig(name="integration-test", type="filesystem", path=str(tmp_path))


@pytest.fixture()
def config(tmp_path: Path, library: LibraryConfig) -> WoofConfig:
    """WoofConfig with a single backend and an isolated config directory."""
    return WoofConfig(
        libraries=[library],
        config_dir=tmp_path / ".woof",
    )


@pytest_asyncio.fixture()
async def agent_client(library: LibraryConfig) -> AgentClient:  # type: ignore[misc]
    """AgentClient that shuts down all sidecars after each test."""
    client = AgentClient()
    yield client  # type: ignore[misc]
    await client.shutdown()
