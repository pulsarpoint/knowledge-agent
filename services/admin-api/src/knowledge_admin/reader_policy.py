"""Which agent configurations may answer Reader users (Knowledge tier)."""
from typing import Any

from knowledge_admin.agents import get_agent_config

NOT_CONFIGURED = "The assistant isn't configured yet. Ask an administrator."
NOT_ALLOWED = "The assistant's configuration isn't allowed in the Reader."


def knowledge_violations(config: dict[str, Any]) -> list[str]:
    """Readable reasons this configuration cannot serve every signed-in Reader user; empty when allowed."""
    reasons = []
    if not config['enabled']:
        reasons.append('agent is disabled')
    if config['model'] is None or not config['model']['enabled']:
        reasons.append('no enabled model')
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


def reader_violations(agent_id: str, conninfo: str) -> list[str]:
    """Why a published agent cannot answer Reader users right now, judged on the configuration the Reader would run."""
    try:
        config = get_agent_config(agent_id, conninfo=conninfo)
    except ValueError as exc:
        return [str(exc)]
    reasons = knowledge_violations(config)
    if reasons and config.get('config_version'):
        # Settings changes (including migration 0060) touch only the working copy, never a saved version.
        reasons.append(f"active version v{config['config_version']['version']} predates these settings"
                       ' — save and activate a new version')
    return reasons
