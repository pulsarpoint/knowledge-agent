"""Tests that build a run configuration by hand give it an access object of explicit entries (as run start builds
them: knowledge_agents.config.resource_entries), all given."""

from typing import Any

from psycopg.types.json import Jsonb


def entry(type_: str, id_, name: str = '', **extra) -> dict[str, Any]:
    """One access entry: {type, id, name, description_short: '', **extra} (a server its connection, a code project
    its folder_paths, ...)."""
    return {'type': type_, 'id': str(id_), 'name': name, 'description_short': '', **extra}


def access_of(entries=(), content_editor: bool = False) -> dict[str, Any]:
    return {
        'resources': [{**item, 'source': 'given'} for item in entries],
        'roles': {'content_editor': content_editor},
    }


def with_access(config: dict[str, Any], entries=(), content_editor: bool = False) -> dict[str, Any]:
    """The configuration with an access object of these entries (set in place, and returned)."""
    config['access'] = access_of(entries, content_editor)
    return config


def give(conn, agent_id: str, resources: list, content_editor: bool = False) -> None:
    """Store a saved agent's access object: resources as (type, id) or {type, id, editable}."""
    entries = [
        {**item, 'id': str(item['id'])} if isinstance(item, dict) else {'type': item[0], 'id': str(item[1])}
        for item in resources
    ]
    conn.execute(
        'UPDATE ka.agents SET access=%s WHERE id=%s',
        (
            Jsonb(
                {
                    'resources': entries,
                    'roles': {'content_editor': content_editor},
                }
            ),
            agent_id,
        ),
    )


def of(config: dict[str, Any], type_: str) -> list[dict[str, Any]]:
    """The entries of one type in a configuration's access object."""
    return [item for item in config['access']['resources'] if item['type'] == type_]


def ids(config: dict[str, Any], type_: str, source: str | None = None) -> list[str]:
    """The ids of one type in a configuration's access object (source 'given': only what the run was given)."""
    return [item['id'] for item in of(config, type_) if source is None or item['source'] == source]
