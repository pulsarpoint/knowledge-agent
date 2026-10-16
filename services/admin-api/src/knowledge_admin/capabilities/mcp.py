"""Which MCP servers an agent connects to and how it authenticates; the connection itself is built by the runner."""

from collections.abc import Mapping
from typing import Any


def selected_servers(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The agent's enabled MCP definitions, each once."""
    if not config['mcp']['enabled']:
        return []
    return list({server['id']: server for server in config['mcp']['servers'] if server['enabled']}.values())


def auth_headers(server: dict[str, Any], credentials: Mapping[str, str]) -> dict[str, str]:
    """The request headers for one MCP server; its token must have been resolved by the backend."""
    reference = server['bearer_token_ref']
    if reference is None:
        return {}
    if not credentials.get(reference['id']):
        raise ValueError(f'The MCP token for {server["name"]} was not supplied.')
    return {'Authorization': 'Bearer ' + credentials[reference['id']]}
