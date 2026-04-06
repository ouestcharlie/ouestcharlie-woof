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
from woof.config import BackendConfig, WoofConfig


@pytest.fixture()
def backend(tmp_path: Path) -> BackendConfig:
    """A minimal backend config pointing at an empty temporary directory."""
    return BackendConfig(name="integration-test", type="local", path=str(tmp_path))


@pytest.fixture()
def config(tmp_path: Path, backend: BackendConfig) -> WoofConfig:
    """WoofConfig with a single backend and an isolated config directory."""
    return WoofConfig(
        backends=[backend],
        config_dir=tmp_path / ".woof",
    )


@pytest_asyncio.fixture()
async def agent_client(backend: BackendConfig) -> AgentClient:  # type: ignore[misc]
    """AgentClient that shuts down all sidecars after each test."""
    client = AgentClient()
    yield client  # type: ignore[misc]
    await client.shutdown()
