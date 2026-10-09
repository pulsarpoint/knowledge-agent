"""Experts: saved agents other agents call as tools (ask_<name>). Which experts a caller is offered, their tool names
and descriptions, pinning them into a session's configuration, and loading one for a call. Running a call is
subagents.Subagents.ask."""
import re
from collections.abc import Mapping
from typing import Any

from knowledge_admin.knowledge_agents.config import get_agent_config, model_fits, per_run
from knowledge_admin.knowledge_agents.run_resources import KINDS, RunResources, resolve

ROW_PREFIX = 'expert:'
UNAVAILABLE = 'Expert {name} is not available.'
FAILED = 'Expert {name} failed: {message}'
MAX_SLUG = 50
# The agents row switch behind each run-resource kind, in KINDS order.
SWITCHES = {'code_project_ids': 'repositories_enabled', 'collection_ids': 'documents_enabled',
            'server_ids': 'ssh_enabled', 'service_ids': 'services_enabled'}
OFFERED = '''SELECT a.id,a.name,a.when_to_use,a.resources,a.repositories_enabled,a.documents_enabled,a.ssh_enabled,
    a.services_enabled,m.provider,m.api_format,m.model
    FROM ka.agents a JOIN ka.llm_models m ON m.id=a.model_id AND m.enabled
    WHERE a.expert AND a.enabled AND a.id IS DISTINCT FROM %(caller)s
      AND NOT EXISTS (SELECT 1 FROM ka.agent_tools t WHERE t.agent_id=%(caller)s AND t.tool='expert:'||a.id
                      AND t.mode='off')
    ORDER BY lower(a.name),a.id'''


def slug(name: str) -> str:
    """'Payments API' -> 'payments_api': lowercase a-z0-9 words joined by _, at most 50 characters."""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:MAX_SLUG].strip('_')


def description(name: str, when_to_use: str, resources: str, accepts: list[str]) -> str:
    text = f'Ask {name}, an expert agent. {when_to_use.strip()}'
    if resources == 'per_run':
        kinds = ', '.join(kind.plural for kind in KINDS if kind.key in accepts)
        if not kinds:
            return f'{text} It works only on the resources you pass.'
        return f'{text} It works only on the resources you pass (it accepts: {kinds}).'
    return f'{text} It uses its own resources.'


def offered(conn, caller_id: str | None) -> list[dict[str, Any]]:
    """The experts this caller may call: experts, enabled, with an enabled agent model, never the caller itself, and not
    switched off on its Tools tab. A Generate documents agent (caller None) gets them all."""
    entries: list[dict[str, Any]] = []
    taken: set[str] = set()
    for row in conn.execute(OFFERED, {'caller': caller_id}):
        if not model_fits(row):
            continue
        base = f"ask_{slug(row['name']) or 'expert'}"
        tool, number = base, 1
        while tool in taken:
            number += 1
            tool = f'{base}_{number}'
        taken.add(tool)
        accepts = [kind.key for kind in KINDS if row[SWITCHES[kind.key]]] if row['resources'] == 'per_run' else []
        entries.append({'id': str(row['id']), 'name': row['name'], 'when_to_use': row['when_to_use'],
                        'resources': row['resources'], 'accepts': accepts, 'tool': tool,
                        'description': description(row['name'], row['when_to_use'], row['resources'], accepts)})
    return entries


def pin(conn, config: dict[str, Any], caller_id: str | None) -> dict[str, Any]:
    """The configuration a session pins, with the experts it may call (later changes apply to new sessions)."""
    return {**config, 'experts': offered(conn, caller_id)}


def load(store, project_id, entry: Mapping[str, Any], resources: Mapping[str, list[str]] | None
         ) -> tuple[dict[str, Any], dict[str, str]]:
    """The expert's configuration for one call (its current settings; per run: resolved with the given resources,
    which the caller already checked are its own) and its decrypted credentials. ValueError: a message for the model.
    Synchronous: the caller runs it in a thread."""
    from knowledge_admin.agents import load_credentials  # agents imports this package

    unavailable = ValueError(UNAVAILABLE.format(name=entry['name']))
    try:
        config = get_agent_config(entry['id'], conninfo=store.conninfo)
    except LookupError:
        raise unavailable from None
    with store.connect() as conn:
        row = conn.execute('SELECT expert FROM ka.agents WHERE id=%s', (entry['id'],)).fetchone()
        model = config['model']
        if row is None or not row['expert'] or not config['enabled'] or model is None or not model['enabled'] \
                or not model_fits(model):
            raise unavailable
        if per_run(config):
            config = resolve(conn, project_id, config, RunResources(**(resources or {})))
    config.pop('record', None)
    config['experts'] = []  # an expert never calls experts
    return config, load_credentials(store, config)
