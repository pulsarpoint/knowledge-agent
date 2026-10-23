"""The procedure fixture uses the ordinary skill library and preserves operator edits."""

from pathlib import Path

import psycopg
import pytest

from test_api import _client
from test_skills import HEADERS


def test_api_analysis_fixture_is_editable_and_not_automatically_assigned(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    skills = client.get('/api/skills', headers=HEADERS).json()['skills']
    skill = next(row for row in skills if row['name'] == 'api-analysis')
    detail = client.get(f'/api/skills/{skill["id"]}', headers=HEADERS).json()
    repo = Path(__file__).resolve().parents[3]
    text = (repo / 'docs/skills/api-analysis/SKILL.md').read_text()
    assert detail['instructions'] == text.split('---\n', 2)[2].lstrip('\n')
    assert detail['enabled'] is True and detail['revision'] == 1
    with store.connect() as conn:
        row = conn.execute('SELECT project_id FROM ka.agent_skills WHERE id=%s', (skill['id'],)).fetchone()
        assert (
            conn.execute(
                """SELECT count(*) AS n FROM ka.agents
                WHERE access->'resources' @> jsonb_build_array(jsonb_build_object('type','skill','id',%s::text))""",
                (skill['id'],),
            ).fetchone()['n']
            == 0
        )
        conn.execute('SELECT ka.seed_api_analysis_skill(%s)', (row['project_id'],))
    response = client.put(
        f'/api/skills/{skill["id"]}',
        headers=HEADERS,
        json={
            'name': 'api-analysis',
            'description': detail['description'],
            'instructions': 'An administrator replacement.',
            'enabled': False,
            'revision': 1,
        },
    )
    assert response.status_code == 200, response.text
    with store.connect() as conn:
        conn.execute('SELECT ka.seed_api_analysis_skill(%s)', (row['project_id'],))
        assert (
            conn.execute(
                'SELECT count(*) AS n FROM ka.agent_skills WHERE project_id=%s', (row['project_id'],)
            ).fetchone()['n']
            == 1
        )
    updated = client.get(f'/api/skills/{skill["id"]}', headers=HEADERS).json()
    assert updated['instructions'] == 'An administrator replacement.' and updated['enabled'] is False
    with store.connect() as conn:
        conn.execute((repo / 'db/migrations/0134_rag_gone.down.sql').read_text(), prepare=False)  # 0114 down reads it
        conn.execute((repo / 'db/migrations/0114_drop_agent_resource_tables.down.sql').read_text(), prepare=False)
        with pytest.raises(psycopg.errors.RaiseException, match='edited or assigned'):
            conn.execute((repo / 'db/migrations/0059_api_analysis_skill.down.sql').read_text(), prepare=False)
