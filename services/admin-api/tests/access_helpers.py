"""Tests that build a run configuration by hand give it the access object run start would: every resource its
sections list (sessions.run_access.config_resources), all given; a partial configuration (document tests) its
documents."""

from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from knowledge_admin.sessions.run_access import config_resources, document_entry

SECTIONS = ('ssh', 'services', 'code', 'documents', 'skills', 'mcp')


def access_of(config: dict[str, Any], content_editor: bool = False) -> dict[str, Any]:
    if all(key in config for key in SECTIONS):
        entries = config_resources(config)
    else:
        entries = [document_entry(config, item) for item in config.get('document_access') or []]
    return {
        'resources': [{**entry, 'source': 'given'} for entry in entries],
        'roles': {'content_editor': content_editor},
    }


def with_access(config: dict[str, Any], content_editor: bool = False) -> dict[str, Any]:
    """The configuration with its access object (set in place, and returned)."""
    config['access'] = access_of(config, content_editor)
    return config


def give(conn, agent_id: str, resources: list, content_editor: bool = False) -> None:
    """Store a saved agent's access object: resources as (type, id) or {type, id, editable}."""
    entries = [
        {**item, 'id': str(item['id'])} if isinstance(item, dict) else {'type': item[0], 'id': str(item[1])}
        for item in resources
    ]
    conn.execute(
        'UPDATE ka.agents SET access=%s WHERE id=%s',
        (Jsonb({'resources': entries, 'roles': {'content_editor': content_editor}}), agent_id),
    )


MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def from_tables(store) -> None:
    """Every agent's access object and instructions converted from the per-type tables, as 0113 converts them (0113
    down, then up): the agent API, the wizard, clones and reviews write those tables until they write the access
    object themselves (phase 2, task 2)."""
    with store.connect() as conn:
        for name in ('0113_agent_access.down.sql', '0113_agent_access.up.sql'):
            conn.execute((MIGRATIONS / name).read_text(), prepare=False)
