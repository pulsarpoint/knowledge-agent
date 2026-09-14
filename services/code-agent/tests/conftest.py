"""Shared fixtures for code-agent tests."""
from __future__ import annotations

import os

import pytest

# Every env var resolve_runtime/load_env_file can touch -- snapshotted and
# restored around each test so a test that sets one (directly, or via
# load_env_file's os.environ.setdefault) never leaks into the next.
CODE_AGENT_ENV_VARS = [
    "CODE_AGENT_BACKEND",
    "CODE_AGENT_MODEL",
    "CODE_AGENT_PROVIDER_BASE_URL",
    "CODE_AGENT_API_KEY",
    "CODE_AGENT_CODEX_COMMAND",
    "CODE_AGENT_TIMEOUT_SECONDS",
]


@pytest.fixture(autouse=True)
def _clean_code_agent_env():
    saved = {key: os.environ.get(key) for key in CODE_AGENT_ENV_VARS}
    for key in CODE_AGENT_ENV_VARS:
        os.environ.pop(key, None)
    yield
    for key in CODE_AGENT_ENV_VARS:
        if saved[key] is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = saved[key]


@pytest.fixture()
def repo(tmp_path):
    """A tmp directory standing in for a checked-out repo."""
    d = tmp_path / "my-app"
    d.mkdir()
    (d / "README.md").write_text("# my-app\n", encoding="utf-8")
    return d
