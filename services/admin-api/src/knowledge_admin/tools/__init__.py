"""Import function tools directly from files.py, ssh.py, and shell.py."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict


class AgentContext(TypedDict):
    """Backend-only inputs passed to Runner through its context argument.

    config is the existing get_agent_config() snapshot. Credentials have already
    been resolved by the caller. Neither field is a model-supplied tool argument.
    shell_enabled may only be true in an execution environment isolated by the
    caller; changing the working directory does not create a shell sandbox.
    """

    config: dict[str, Any]
    credentials: Mapping[str, str]
    workspace: Path
    shell_enabled: bool
