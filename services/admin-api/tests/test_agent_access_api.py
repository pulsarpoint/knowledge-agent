"""Phase 2: the agent API, the wizard, clones and reviews read and write the access object (ka.agents.access) and the
agent's instructions (ka.agents.instructions). The per-type id lists, switches and per-agent server settings are gone."""

from access_helpers import give, of
from uuid import uuid4

from psycopg.types.json import Jsonb

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.agents.profiles_api import AccessInput, counts, references, resolve
from knowledge_admin.knowledge_agents.config import get_agent_config
from session_rows import a_model, add_review, add_test_session
from test_profiles import create, wizard_payload
from test_repositories import HEADERS

EMPTY = {'resources': []}


def documents(world) -> dict[str, str]:
    with world.store.connect() as conn:
        return {
            row['title']: str(row['id'])
            for row in conn.execute('SELECT id,title FROM ka.live_documents WHERE project_id=%s', (world.project,))
        }


def stored(world, agent_id: str) -> dict:
    with world.store.connect() as conn:
        return conn.execute('SELECT access,instructions FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()


def body(agent: dict, **changes) -> dict:
    """A PUT body from a GET: the access references as GET returns them (extra keys are ignored)."""
    keys = ('revision', 'name', 'description', 'when_to_use', 'enabled', 'documents_grounded', 'access', 'instructions')
    return {**{key: agent[key] for key in keys}, **changes}


FOUND = {
    'server': [{'type': 'server', 'id': 's1', 'name': 'web-1', 'description_short': 'Front'}],
    'document': [{'type': 'document', 'id': 'd1', 'name': 'Runbook', 'description_short': ''}],
}


def test_the_access_rules_without_a_database():
    stored = {
        'resources': [
            {'type': 'server', 'id': 's1'},
            {'type': 'document', 'id': 'd1'},
            {'type': 'server', 'id': 'gone'},
        ],
    }
    assert resolve(stored, FOUND) == {
        'resources': [
            FOUND['server'][0],
            {**FOUND['document'][0]},
            {'type': 'server', 'id': 'gone', 'name': '', 'description_short': '', 'missing': True},
        ],
    }
    assert counts(stored, FOUND)['server'] == 1 and counts(stored, FOUND)['document'] == 1
    s1, d1 = str(uuid4()), str(uuid4())
    found = {
        'server': [{'type': 'server', 'id': s1, 'name': 'a', 'description_short': ''}],
        'document': [{'type': 'document', 'id': d1, 'name': 'b', 'description_short': ''}],
    }
    value = AccessInput.model_validate(
        {
            'resources': [
                {'type': 'server', 'id': s1, 'name': 'ignored'},
                {'type': 'server', 'id': s1},
                {'type': 'document', 'id': d1},
                {'type': 'document', 'id': str(uuid4())},
            ],
        }
    )
    assert references(value, found) == {
        'resources': [{'type': 'server', 'id': s1}, {'type': 'document', 'id': d1}],
    }


def test_get_resolves_each_reference_and_marks_a_missing_one(world):
    w = world
    agent = create(w.client, 'Reader')
    runbook, gone = documents(w)['nginx runbook'], str(uuid4())
    with w.store.connect() as conn:
        give(
            conn,
            agent['id'],
            [
                ('server', w.server['id']),
                ('environment', w.environment),
                {'type': 'document', 'id': runbook},
                ('service', gone),
            ],
        )
        conn.execute("UPDATE ka.agents SET instructions='Check twice.' WHERE id=%s", (agent['id'],))
    found = w.client.get(f'/api/agents/{agent["id"]}', headers=HEADERS).json()
    assert found['instructions'] == 'Check twice.'
    assert found['access'] == {
        'resources': [
            {'type': 'server', 'id': w.server['id'], 'name': 'web-1', 'description_short': 'Shop front end'},
            {'type': 'environment', 'id': str(w.environment), 'name': 'Production', 'description_short': 'Live'},
            {
                'type': 'document',
                'id': runbook,
                'name': 'nginx runbook',
                'description_short': 'About nginx runbook',
            },
            {'type': 'service', 'id': gone, 'name': '', 'description_short': '', 'missing': True},
        ],
    }
    for gone_field in (
        'collection_ids',
        'repository_ids',
        'service_ids',
        'skill_ids',
        'all_collections',
        'ssh_enabled',
    ):
        assert gone_field not in found


def test_put_saves_references_drops_missing_ones_and_refuses_an_unknown_type(world):
    w = world
    agent = create(w.client, 'Writer')
    overview = documents(w)['web-1 overview']
    access = {
        'resources': [
            {'type': 'server', 'id': w.server['id'], 'editable': True},  # an old key: ignored
            {'type': 'service', 'id': str(w.nginx)},
            {'type': 'service', 'id': str(w.nginx)},  # once
            {'type': 'document', 'id': overview},
            {'type': 'code_project', 'id': str(uuid4())},  # no longer exists: dropped
            {'type': 'collection', 'id': str(w.environment)},  # not a collection: dropped
        ],
    }
    saved = w.client.put(
        f'/api/agents/{agent["id"]}', headers=HEADERS, json=body(agent, access=access, instructions='Be brief.')
    )
    assert saved.status_code == 200, saved.text
    row = stored(w, agent['id'])
    assert row['instructions'] == 'Be brief.'
    assert row['access'] == {
        'resources': [
            {'type': 'server', 'id': w.server['id']},
            {'type': 'service', 'id': str(w.nginx)},
            {'type': 'document', 'id': overview},
        ],
    }
    assert [r['type'] for r in saved.json()['access']['resources']] == ['server', 'service', 'document']
    config = get_agent_config(agent['id'], conninfo=w.store.conninfo)
    assert [s['id'] for s in of(config, 'server')] == [w.server['id']]
    assert config['instructions'] == 'Be brief.'
    again = saved.json()  # GET's resolved shape can be sent back
    assert w.client.put(f'/api/agents/{agent["id"]}', headers=HEADERS, json=body(again)).status_code == 200
    assert stored(w, agent['id'])['access'] == row['access']
    latest = w.client.get(f'/api/agents/{agent["id"]}', headers=HEADERS).json()
    unknown = {
        'resources': [{'type': 'printer', 'id': str(uuid4())}],
    }
    refused = w.client.put(f'/api/agents/{agent["id"]}', headers=HEADERS, json=body(latest, access=unknown))
    assert refused.status_code == 400 and "'server'" in refused.text, refused.text
    old_fields = w.client.put(
        f'/api/agents/{agent["id"]}', headers=HEADERS, json=body(latest, collection_ids=[], ssh_enabled=True)
    )
    assert old_fields.status_code == 400


def test_options_list_every_project_resource_by_type_and_the_list_counts_access(world):
    w = world
    agent = create(w.client, 'Picker')
    options = w.client.get(f'/api/agents/{agent["id"]}/options', headers=HEADERS)
    assert options.status_code == 200, options.text
    found = options.json()
    assert list(found) == [
        'server',
        'service',
        'environment',
        'code_project',
        'collection',
        'document',
        'skill',
        'mcp_server',
    ]
    assert found['server'] == [
        {'type': 'server', 'id': w.server['id'], 'name': 'web-1', 'description_short': 'Shop front end'}
    ]
    assert found['service'] == [
        {'type': 'service', 'id': str(w.nginx), 'name': 'nginx', 'description_short': 'Front proxy for the shop'}
    ]
    assert [d['name'] for d in found['document']] == ['nginx runbook', 'web-1 overview']
    assert found['code_project'][0]['id'] == str(w.shop) and found['mcp_server'] == []
    with w.store.connect() as conn:
        give(conn, agent['id'], [('server', w.server['id']), ('service', w.nginx), ('service', uuid4())])
    listed = next(a for a in w.client.get('/api/agents', headers=HEADERS).json()['agents'] if a['id'] == agent['id'])
    assert listed['counts'] == {
        'server': 1,
        'service': 1,  # the missing one is not counted
        'environment': 0,
        'code_project': 0,
        'collection': 0,
        'document': 0,
        'skill': 0,
        'mcp_server': 0,
    }
    assert 'accepts' not in listed


def test_the_wizard_creates_an_agent_with_its_access_and_instructions(world):
    w = world
    payload = wizard_payload(
        'Shop operator',
        instructions='Read the logs first.',
        access={
            'resources': [{'type': 'server', 'id': w.server['id']}, {'type': 'service', 'id': str(w.nginx)}],
        },
    )
    created = w.client.post('/api/agents/wizard', headers=HEADERS, json=payload)
    assert created.status_code == 201, created.text
    row = stored(w, created.json()['id'])
    assert row['instructions'] == 'Read the logs first.'
    assert row['access'] == {
        'resources': [{'type': 'server', 'id': w.server['id']}, {'type': 'service', 'id': str(w.nginx)}],
    }
    missing = wizard_payload(
        'Other',
        access={
            'resources': [{'type': 'server', 'id': str(uuid4())}],
        },
    )
    refused = w.client.post('/api/agents/wizard', headers=HEADERS, json=missing)
    assert refused.status_code == 400, refused.text
    options = w.client.get('/api/agents/wizard/options', headers=HEADERS).json()
    assert 'access' in options['schema']['properties'] and 'servers' not in options['schema']['properties']
    assert {'environments', 'documents'} <= set(options['catalog'])


def test_a_clone_copies_access_and_instructions_and_apply_writes_them_back(world):
    w = world
    agent = create(w.client, 'Original')
    with w.store.connect() as conn:
        give(conn, agent['id'], [('server', w.server['id'])])
        conn.execute("UPDATE ka.agents SET instructions='Original notes.' WHERE id=%s", (agent['id'],))
    copy_id = w.client.post(f'/api/agents/{agent["id"]}/clone', headers=HEADERS).json()['id']
    assert stored(w, copy_id) == stored(w, agent['id'])
    copied = w.client.get(f'/api/agents/{copy_id}', headers=HEADERS).json()
    changed = {
        'resources': [{'type': 'environment', 'id': str(w.environment)}],
    }
    saved = w.client.put(
        f'/api/agents/{copy_id}', headers=HEADERS, json=body(copied, access=changed, instructions='New.')
    )
    assert saved.status_code == 200, saved.text
    preview = w.client.get(f'/api/agents/{copy_id}/apply', headers=HEADERS).json()
    assert preview['changes'] == ['General', 'Resources']  # the tabs: instructions are on General
    applied = w.client.post(
        f'/api/agents/{copy_id}/apply',
        headers=HEADERS,
        json={'revision': preview['original']['revision'], 'fingerprint': preview['fingerprint']},
    )
    assert applied.status_code == 200, applied.text
    assert stored(w, agent['id']) == {'access': changed, 'instructions': 'New.'}


def test_a_review_writes_the_agents_instructions(world):
    w = world
    agent = create(w.client, 'Reviewed')
    with w.store.connect() as conn:
        give(conn, agent['id'], [('server', w.server['id'])])
        project, actor = w.project, w.store.actor(conn, 'reviewer', 'reviewer@test')
        session = add_test_session(conn, project, actor, agent['id'], a_model(conn, actor), status='waiting', turns=1)
        review = str(add_review(conn, session, actor, changes={'instructions': 'Cite.'}))
    source = w.client.get(f'/api/agents/{agent["id"]}/reviews/{review}', headers=HEADERS).json()
    assert 'servers' not in source and source['instructions'] == ''
    applied = w.client.post(
        f'/api/agents/{agent["id"]}/reviews/{review}/apply',
        headers=HEADERS,
        json={
            'target': 'agent',
            'revision': source['revision'],
            'fingerprint': source['fingerprint'],
            'instructions': 'Cite sources.',
        },
    )
    assert applied.status_code == 200, applied.text
    assert stored(w, agent['id'])['instructions'] == 'Cite sources.'


def test_the_agent_parts_of_operations_are_gone(world):
    w = world
    agent = create(w.client, 'Gone')
    server = w.server['id']
    for method, path in (
        ('get', '/api/operations/settings'),
        ('put', '/api/operations/settings'),
        ('put', f'/api/operations/configs/{server}'),
        ('delete', f'/api/operations/configs/{server}'),
        ('get', f'/api/agents/{agent["id"]}/servers/{server}/ssh'),
        ('put', f'/api/agents/{agent["id"]}/servers/{server}/ssh'),
        ('delete', f'/api/agents/{agent["id"]}/servers/{server}/ssh'),
        ('post', f'/api/agents/{agent["id"]}/servers/{server}/ssh/test'),
    ):
        response = w.client.request(method.upper(), path, headers=HEADERS, json={})
        assert response.status_code in (404, 405), (method, path, response.status_code)
    assert w.client.get(f'/api/servers/{server}/ssh', headers=HEADERS).status_code == 200  # the server's own stays


def test_put_refuses_roles_and_an_old_stored_role_or_editable_reads_as_gone(world):
    w = world
    agent = create(w.client, 'Inventory keeper')
    access = {'resources': [], 'roles': {}}
    refused = w.client.put(f'/api/agents/{agent["id"]}', headers=HEADERS, json=body(agent, access=access))
    assert refused.status_code == 400, refused.text  # roles are gone: an unknown key
    runbook = documents(w)['nginx runbook']
    with w.store.connect() as conn:  # stored before the roles went
        conn.execute(
            'UPDATE ka.agents SET access=%s WHERE id=%s',
            (
                Jsonb(
                    {
                        'resources': [{'type': 'document', 'id': runbook, 'editable': True}],
                        'roles': {'content_editor': True, 'inventory_editor': True},
                    }
                ),
                agent['id'],
            ),
        )
    found = w.client.get(f'/api/agents/{agent["id"]}', headers=HEADERS).json()
    assert 'roles' not in found['access'] and 'editable' not in found['access']['resources'][0]
    saved = w.client.put(f'/api/agents/{agent["id"]}', headers=HEADERS, json=body(found, access=found['access']))
    assert saved.status_code == 200, saved.text
    assert stored(w, agent['id'])['access'] == {'resources': [{'type': 'document', 'id': runbook}]}
