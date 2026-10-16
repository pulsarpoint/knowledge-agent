"""tools.json: the attributes of each built-in tool used to build agents (group, label, summary), in the order
the Tools tab and the runner use. tools/catalog.py reads it at import; sync_tools copies it into ka.tools at the
admin-api start, so the table always equals the file."""

import json
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).with_name('tools.json')


def load_fixture(path: Path = FIXTURE) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def sync_tools(store, entries: list[dict[str, Any]] | None = None) -> None:
    """Replace ka.tools with the fixture in one transaction; concurrent starts wait for each other."""
    entries = load_fixture() if entries is None else entries
    with store.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('ka.tools'))")
        conn.execute('DELETE FROM ka.tools')
        with conn.cursor() as cursor:
            cursor.executemany(
                'INSERT INTO ka.tools(name,position,tool_group,label,summary) VALUES (%s,%s,%s,%s,%s)',
                [
                    (entry['name'], position, entry['group'], entry['label'], entry['summary'])
                    for position, entry in enumerate(entries)
                ],
            )
