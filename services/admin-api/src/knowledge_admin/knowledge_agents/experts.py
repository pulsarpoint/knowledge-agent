"""Experts: saved agents other agents call as tools (ask_<name>). Which experts a caller is offered, their tool names
and descriptions, pinning them into a session's configuration, and loading one for a call. Running a call is
subagents.Subagents.ask."""

import re
from collections.abc import Mapping
from typing import Any

from knowledge_admin import access as access_rules
from knowledge_admin.knowledge_agents.config import access_ids, get_agent_config, model_fits, without_missing
from knowledge_admin.knowledge_agents.run_resources import KINDS, picks

UNAVAILABLE = 'Expert {name} is not available.'
FAILED = 'Expert {name} failed: {message}'
MAX_SLUG = 50
OFFERED = """SELECT a.id,a.name,a.when_to_use,a.access,m.provider,m.api_format,m.model
    FROM ka.agents a JOIN ka.llm_models m ON m.id=a.model_id AND m.enabled
    WHERE a.expert AND a.enabled AND a.id IS DISTINCT FROM %(caller)s
    ORDER BY lower(a.name),a.id"""


def slug(name: str) -> str:
    """'Payments API' -> 'payments_api': lowercase a-z0-9 words joined by _, at most 50 characters."""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:MAX_SLUG].strip('_')


def description(name: str, when_to_use: str, accepts: list[str]) -> str:
    text = f'Ask {name}, an expert agent. {when_to_use.strip()}'
    if accepts:
        kinds = ', '.join(kind.plural for kind in KINDS if kind.key in accepts)
        return f'{text} It uses its own resources; pass some of them to narrow it to those (it accepts: {kinds}).'
    return f'{text} It uses its own resources.'


def offered(conn, caller_id: str | None) -> list[dict[str, Any]]:
    """The experts this caller may call: experts, enabled, with an enabled agent model, never the caller itself. An
    ad-hoc run (caller None) gets them all."""
    entries: list[dict[str, Any]] = []
    taken: set[str] = set()
    for row in conn.execute(OFFERED, {'caller': caller_id}):
        if not model_fits(row):
            continue
        base = f'ask_{slug(row["name"]) or "expert"}'
        tool, number = base, 1
        while tool in taken:
            number += 1
            tool = f'{base}_{number}'
        taken.add(tool)
        held = without_missing(conn, row['access'])
        accepts = [kind.key for kind in KINDS if access_ids(held, kind.type)]  # the kinds in its access that exist
        entries.append(
            {
                'id': str(row['id']),
                'name': row['name'],
                'when_to_use': row['when_to_use'],
                'accepts': accepts,
                'tool': tool,
                'description': description(row['name'], row['when_to_use'], accepts),
            }
        )
    return entries


def pin(conn, config: dict[str, Any], caller_id: str | None) -> dict[str, Any]:
    """The configuration a session pins, with the experts it may call (later changes apply to new sessions)."""
    return {**config, 'experts': offered(conn, caller_id)}


def load(
    store,
    project_id,
    entry: Mapping[str, Any],
    resources: Mapping[str, list[str]] | None,
    caller_content_editor: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """The expert's configuration for one call (its current settings), with its access object: its own agent's access,
    narrowed to the given resources (which the caller already checked are its own; none given: everything it has),
    a content editor only when its agent and the calling run both are; and its decrypted credentials. ValueError: a message for the model (also a given resource
    outside its access). Synchronous: the caller runs it in a thread."""
    from knowledge_admin.agents.runner import load_credentials  # agents imports this package
    from knowledge_admin.sessions.run_access import agent_access  # sessions imports this package

    unavailable = ValueError(UNAVAILABLE.format(name=entry['name']))
    try:
        config = get_agent_config(entry['id'], conninfo=store.conninfo)
    except LookupError:
        raise unavailable from None
    with store.connect() as conn:
        row = conn.execute('SELECT expert FROM ka.agents WHERE id=%s', (entry['id'],)).fetchone()
        model = config['model']
        if (
            row is None
            or not row['expert']
            or not config['enabled']
            or model is None
            or not model['enabled']
            or not model_fits(model)
        ):
            raise unavailable
        # its access object, built as a run start builds one for its agent
        access = agent_access(
            conn,
            project_id,
            config,
            picks(resources) or None,
            content_editor=caller_content_editor and access_rules.is_content_editor(config['access']),
        )
        config = {**config, 'access': access, 'project_id': str(project_id)}
    config.pop('record', None)
    config['experts'] = []  # an expert never calls experts
    return config, load_credentials(store, config)
