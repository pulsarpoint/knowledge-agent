"""Import decorated function tools directly from their modules."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict


class AgentContext(TypedDict):
    """Backend-only inputs passed to Runner through its context argument.

    config is the existing get_agent_config() snapshot. Credentials have already
    been resolved by the caller. Neither field is a model-supplied tool argument.
    shell_enabled permits the shell tool to call the isolated execution worker;
    commands are never executed inside the administration process.
    """

    config: dict[str, Any]
    credentials: Mapping[str, str]
    workspace: Path
    shell_enabled: bool
