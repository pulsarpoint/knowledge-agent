"""Which MCP servers an agent connects to and how it authenticates; the connection itself is built by the runner."""

from collections.abc import Mapping
from typing import Any


def selected_servers(config: dict[str, Any], ssh_servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enabled MCP definitions of the agent and of its selected SSH servers, each once."""
    if not config['mcp']['enabled']:
        return []
    servers = list(config['mcp']['servers'])
    for server in ssh_servers:
        servers.extend(server['mcp_servers'])
    return list({server['id']: server for server in servers if server['enabled']}.values())


def auth_headers(server: dict[str, Any], credentials: Mapping[str, str]) -> dict[str, str]:
    """The request headers for one MCP server; its token must have been resolved by the backend."""
    reference = server['bearer_token_ref']
    if reference is None:
        return {}
    if not credentials.get(reference['id']):
        raise ValueError(f'The MCP token for {server["name"]} was not supplied.')
    return {'Authorization': 'Bearer ' + credentials[reference['id']]}
