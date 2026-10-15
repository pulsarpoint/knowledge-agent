"""The migration template works on upgrades and lazily initialized installations."""

from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS


MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
NAME = 'Application documentation (Diátaxis)'
ENDPOINT = '/api/documentation/prompts'


def test_fresh_workspace_fixture_is_active_and_edits_are_drafts(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.get(ENDPOINT, headers=HEADERS)
    assert response.status_code == 200, response.text
    fixture = next(p for p in response.json()['prompts'] if p['name'] == NAME)
    assert fixture['name'] == NAME
    assert fixture['tags'] == ['documentation', 'code'] and fixture['enabled']
    assert fixture['active_version'] == 1 and fixture['version_count'] == 1
    endpoint = ENDPOINT + '/' + fixture['id']
    original = client.get(endpoint, headers=HEADERS).json()
    [version1] = original['versions']
    assert version1['id'] == original['active_version_id']
    assert version1['activated_at'] is not None and version1['created_by'] is None

    edit = {key: original[key] for key in ('name', 'description', 'tags', 'enabled', 'revision')}
    edit['content'] = version1['content'] + '\nPrioritize the incident response runbook.\n'
    updated = client.put(endpoint, headers=HEADERS, json=edit)
    assert updated.status_code == 200, updated.text
    current = client.get(endpoint, headers=HEADERS).json()
    assert current['active_version_id'] == version1['id']
    assert current['versions'][0]['version'] == 2
    assert current['versions'][0]['activated_at'] is None
    assert current['versions'][0]['content'] == edit['content']

    with store.connect() as conn:
        conn.execute('SELECT ka.seed_app_docs_prompt(%s)', (UUID(fixture['project_id']),))
    assert client.get(endpoint, headers=HEADERS).json() == current
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='has been customized'):
            conn.execute((MIGRATIONS / '0041_app_docs_prompt.down.sql').read_text(), prepare=False)
        conn.rollback()
    assert client.get(endpoint, headers=HEADERS).json() == current


def test_upgrade_backfills_existing_workspace_and_preserves_same_name_prompt(store, tmp_path):
    with store.connect() as conn:
        # 0104's views read documents.phase_metadata, which 0073's rollback drops: roll 0104 back first (0107 before it).
        conn.execute((MIGRATIONS / '0109_tags_not_flags.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0107_inventory_document.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0104_document_model.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0073_remove_documentation_generation.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0058_graph_directed_api_documentation.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0057_bounded_api_documentation.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0056_messaging_documentation.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0055_documentation_sessions.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0054_api_documentation.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0053_application_overview.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0052_codegraph_documentation.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0049_layered_environment_architecture.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0048_server_infrastructure_documentation.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0047_documentation_workflows.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0046_documentation_generation.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0041_app_docs_prompt.down.sql').read_text(), prepare=False)
        existing_project = store.project(conn, 'existing')
    client = _client(tmp_path, repository_store=store)
    # Case-insensitive collisions must leave an administrator's template untouched.
    custom = {'name': NAME.upper(), 'tags': ['code'], 'content': 'Our existing instructions.'}
    created = client.post(ENDPOINT, headers=HEADERS, json=custom)
    assert created.status_code == 201, created.text
    endpoint = ENDPOINT + '/' + created.json()['id']
    before = client.get(endpoint, headers=HEADERS).json()

    with store.connect() as conn:
        conn.execute((MIGRATIONS / '0041_app_docs_prompt.up.sql').read_text(), prepare=False)
        fixture = conn.execute(
            """SELECT p.*,v.version FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
            WHERE p.project_id=%s AND NOT (p.tags && ARRAY['server-discovery','document-revision','new-document','assistant'])""",
            (existing_project,),
        ).fetchone()
        assert fixture['name'] == NAME and fixture['version'] == 1
        conn.execute('SELECT ka.seed_app_docs_prompt(%s)', (existing_project,))
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM ka.documentation_prompts WHERE NOT (tags && ARRAY['server-discovery','document-revision','new-document','assistant'])"
            ).fetchone()['n']
            == 2
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM ka.audit_events WHERE action='documentation_prompt.seeded'"
            ).fetchone()['n']
            == 1
        )
        conn.execute((MIGRATIONS / '0041_app_docs_prompt.down.sql').read_text(), prepare=False)
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM ka.documentation_prompts WHERE project_id=%s AND NOT (tags && ARRAY['server-discovery','document-revision','new-document','assistant'])",
                (existing_project,),
            ).fetchone()['n']
            == 0
        )
        assert (
            conn.execute(
                'SELECT count(*) AS n FROM ka.documentation_prompt_versions WHERE prompt_id=%s', (fixture['id'],)
            ).fetchone()['n']
            == 0
        )
        conn.execute((MIGRATIONS / '0041_app_docs_prompt.up.sql').read_text(), prepare=False)
        assert (
            conn.execute(
                "SELECT id FROM ka.documentation_prompts WHERE project_id=%s AND NOT (tags && ARRAY['server-discovery','document-revision','new-document','assistant'])",
                (existing_project,),
            ).fetchone()['id']
            == fixture['id']
        )
    assert client.get(endpoint, headers=HEADERS).json() == before


def test_deleted_fixture_is_not_recreated_by_normal_requests(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    fixture = next(p for p in client.get(ENDPOINT, headers=HEADERS).json()['prompts'] if p['name'] == NAME)
    endpoint = ENDPOINT + '/' + fixture['id']
    deleted = client.delete(endpoint, headers=HEADERS, params={'revision': fixture['revision'], 'confirmed': True})
    assert deleted.status_code == 200, deleted.text
    assert all(p['id'] != fixture['id'] for p in client.get(ENDPOINT, headers=HEADERS).json()['prompts'])
    assert client.get(endpoint, headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert (
            conn.execute(
                'SELECT count(*) AS n FROM ka.documentation_prompt_versions WHERE prompt_id=%s', (UUID(fixture['id']),)
            ).fetchone()['n']
            == 0
        )
        conn.execute((MIGRATIONS / '0041_app_docs_prompt.down.sql').read_text(), prepare=False)
