"""Which agent configurations may answer Reader users (Knowledge tier)."""
from typing import Any

NOT_CONFIGURED = "The assistant isn't configured yet. Ask an administrator."
NOT_ALLOWED = "The assistant's configuration isn't allowed in the Reader."


def knowledge_violations(config: dict[str, Any]) -> list[str]:
    """Readable reasons this configuration cannot serve every signed-in Reader user; empty when allowed."""
    reasons = []
    if config['ssh']['enabled']:
        reasons.append('uses SSH')
    if config['mcp']['enabled']:
        reasons.append('uses MCP tools')
    if config['code']['enabled']:
        reasons.append('uses code projects')
    if not config['documents']['enabled']:
        reasons.append('document search is off')
    elif not config['documents'].get('grounded', False):
        reasons.append('Answer only from documents is off')
    return reasons
