"""Tests that build a run configuration by hand give it the access object run start would: every resource its
sections list (sessions.run_access.config_resources), all given; a partial configuration (document tests) its
documents."""

from typing import Any

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
