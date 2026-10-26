"""Code project dependencies (0143): a code project depends on another code project (a library). The rule, the store
(create / update / delete with revisions and audit; drafts confirmed and discarded alone), the REST API (the
dependencies of a code project with the derived used-by services), the agent tools (save_relation kind
code_depends_on, remove_inventory_item kind code_dependency, get_resource) and the services view of the map."""

import asyncio
import json
from uuid import uuid4

import pytest
from knowledge_admin.capabilities import inventory as inventory_capability
from knowledge_admin.capabilities import inventory_tools as tools
from knowledge_admin.inventory import rules
from knowledge_admin.inventory import store as inventory
from knowledge_admin.inventory.rules import Invalid
from knowledge_admin.tools.catalog import CATALOG
from session_rows import a_model, add_chat_session
from test_api import _client
from test_repositories import HEADERS

SELF = 'A code project cannot depend on itself.'


def duplicate(name: str) -> str:
    return f'This code project already depends on {name}.'


# --- the rule (pure) ---


def test_the_rule_normalizes_and_refuses_self_and_unknown_fields():
    a, b = uuid4(), uuid4()
    assert rules.code_dependency({'from_code_project_id': str(a), 'to_code_project_id': b, 'description': ' x '}) == {
        'from_code_project_id': a,
        'to_code_project_id': b,
        'description': 'x',
    }
    with pytest.raises(Invalid, match=SELF):
        rules.code_dependency({'from_code_project_id': a, 'to_code_project_id': a})
    for values in (
        {'from_code_project_id': a},
        {'to_code_project_id': b},
        {'from_code_project_id': a, 'to_code_project_id': b, 'kind': 'depends_on'},
        {'from_code_project_id': a, 'to_code_project_id': b, 'description': 'x' * 4001},
        {'from_code_project_id': 'nope', 'to_code_project_id': b},
    ):
        with pytest.raises(Invalid):
            rules.code_dependency(values)
    assert rules.duplicate_dependency('lib') == duplicate('lib')


def test_a_draft_dependency_needs_nothing_and_nothing_needs_it():
    a, b, dependency = uuid4(), uuid4(), uuid4()
    rows = {
        **{kind: {} for kind in rules.DRAFT_KINDS},
        'code_project': {a: {'id': a, 'name': 'app'}, b: {'id': b, 'name': 'lib'}},
        'code_dependency': {
            dependency: {'id': dependency, 'from_code_project_id': a, 'to_code_project_id': b, 'status': 'draft'}
        },
    }
    assert rules.confirm_reach('code_dependency', dependency, rows) == [('code_dependency', dependency)]
    assert rules.discard_reach('code_dependency', dependency, rows) == [('code_dependency', dependency)]
    assert rules.entry_name('code_dependency', dependency, rows) == 'app depends on lib'


# --- the store ---


def code_project(conn, project, name: str, archived: bool = False):
    return conn.execute(
        """INSERT INTO ka.repositories(project_id,slug,name,archived_at) VALUES (%s,%s,%s,
        CASE WHEN %s THEN now() END) RETURNING id""",
        (project, name, name, archived),
    ).fetchone()['id']


@pytest.fixture
def world(store):
    """Project shop: a person; code projects app, lib, util and old (archived); services api (built from app) and web
    (built from util)."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'ops', 'ops@example.test')
        conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('ops-agent','Ops agent','')")
        author = {'actor_id': actor}
        w = {'project': project, 'actor': actor, 'author': author}
        for name in ('app', 'lib', 'util'):
            w[name] = code_project(conn, project, name)
        w['old'] = code_project(conn, project, 'old', archived=True)
        for name, built_from in (('api', 'app'), ('web', 'util')):
            w[name] = inventory.create_service(conn, project, {'name': name}, author)['id']
            inventory.create_relation(
                conn,
                project,
                {'from_service_id': w[name], 'kind': 'built_from', 'to_code_project_id': w[built_from]},
                author,
            )
    return w


def depend(conn, w, start: str, end: str, status: str = 'confirmed', **values) -> dict:
    item = {'from_code_project_id': w[start], 'to_code_project_id': w[end], **values}
    return inventory.create_code_dependency(conn, w['project'], item, w['author'], status)


def audit(conn, resource_id) -> list[str]:
    return [
        row['action']
        for row in conn.execute('SELECT action FROM ka.audit_events WHERE resource_id=%s ORDER BY id', (resource_id,))
    ]


def test_create_update_and_delete_with_revisions_and_audit(store, world):
    p, author = world['project'], world['author']
    with store.connect() as conn:
        saved = depend(conn, world, 'app', 'lib', description='http client')
        assert (saved['from_code_project_id'], saved['to_code_project_id']) == (world['app'], world['lib'])
        assert (saved['description'], saved['status'], saved['revision'], saved['created_by']) == (
            'http client',
            'confirmed',
            1,
            world['actor'],
        )
        changed = inventory.update_code_dependency(conn, p, saved['id'], 1, {'description': 'retries'}, author)
        assert (changed['description'], changed['revision']) == ('retries', 2)
        with pytest.raises(inventory.Stale):
            inventory.delete_code_dependency(conn, p, saved['id'], 1, author)
        inventory.delete_code_dependency(conn, p, saved['id'], 2, author)
        assert audit(conn, saved['id']) == [
            'code_dependency.created',
            'code_dependency.updated',
            'code_dependency.deleted',
        ]
        assert inventory.list_code_dependencies(conn, p) == []


def test_a_duplicate_self_archived_or_missing_code_project_is_refused(store, world):
    with store.connect() as conn:
        depend(conn, world, 'app', 'lib')
        with pytest.raises(inventory.Conflict, match=duplicate('lib')):
            depend(conn, world, 'app', 'lib')
        with pytest.raises(Invalid, match=SELF):
            depend(conn, world, 'app', 'app')
        with pytest.raises(Invalid, match='Choose a code project that is not archived'):
            depend(conn, world, 'app', 'old')
        with pytest.raises(Invalid, match='Choose an existing code project'):
            inventory.create_code_dependency(
                conn, world['project'], {'from_code_project_id': world['app'], 'to_code_project_id': uuid4()}, {}
            )
        depend(conn, world, 'lib', 'app')  # a cycle is allowed


def test_a_draft_is_confirmed_or_discarded_alone(store, world):
    p, author = world['project'], world['author']
    with store.connect() as conn:
        draft = depend(conn, world, 'app', 'lib', status='draft')
        other = depend(conn, world, 'util', 'lib', status='draft')
        assert inventory.confirm(conn, p, 'code_dependency', draft['id'], 1, author) == [
            {'kind': 'code_dependency', 'id': draft['id'], 'name': 'app depends on lib'}
        ]
        assert inventory.get(conn, p, 'code_dependency', draft['id'])['status'] == 'confirmed'
        with pytest.raises(Invalid, match='already confirmed'):
            inventory.confirm(conn, p, 'code_dependency', draft['id'], 2, author)
        with pytest.raises(Invalid, match='Only a draft'):
            inventory.discard(conn, p, 'code_dependency', draft['id'], 2, author)
        assert inventory.discard(conn, p, 'code_dependency', other['id'], 1, author) == [
            {'kind': 'code_dependency', 'id': other['id'], 'name': 'util depends on lib'}
        ]
        assert audit(conn, other['id']) == ['code_dependency.created', 'code_dependency.discarded']
        assert [row['id'] for row in inventory.list_code_dependencies(conn, p)] == [draft['id']]


def test_used_by_lists_the_dependent_code_projects_and_the_services_built_from_them(store, world):
    """A library's used-by: the code projects that depend on it and the services built from those — not the services
    built from the library itself, and nothing further away."""
    p = world['project']
    with store.connect() as conn:
        depend(conn, world, 'app', 'lib', description='client')
        depend(conn, world, 'util', 'lib', status='draft')
        depend(conn, world, 'lib', 'util')  # lib's own dependency, and a cycle
        inventory.create_relation(
            conn, p, {'from_service_id': world['web'], 'kind': 'built_from', 'to_code_project_id': world['lib']}, {}
        )
        conn.execute(
            "UPDATE ka.service_relations SET status='draft' WHERE from_service_id=%s AND to_code_project_id=%s",
            (world['web'], world['util']),
        )
        found = inventory.code_project_dependencies(conn, p, world['lib'])
    assert [(d['code_project']['name'], d['status']) for d in found['depends_on']] == [('util', 'confirmed')]
    assert [(d['code_project']['name'], d['status'], d['description']) for d in found['used_by']['code_projects']] == [
        ('app', 'confirmed', 'client'),
        ('util', 'draft', ''),
    ]
    assert [(s['name'], [(v['name'], v['status']) for v in s['via']]) for s in found['used_by']['services']] == [
        ('api', [('app', 'confirmed')]),
        ('web', [('util', 'draft')]),
    ]
    assert set(found['depends_on'][0]) == {'id', 'revision', 'status', 'description', 'code_project'}


# --- the API ---


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def test_the_api_creates_lists_confirms_and_deletes(client, store, world):
    body = {'from_code_project_id': str(world['app']), 'to_code_project_id': str(world['lib'])}
    created = client.post('/api/code-dependencies', headers=HEADERS, json=body | {'description': 'client'})
    assert created.status_code == 201, created.text
    dependency = created.json()
    assert dependency['status'] == 'confirmed'
    again = client.post('/api/code-dependencies', headers=HEADERS, json=body)
    assert (again.status_code, again.json()['error']['message']) == (409, duplicate('lib'))
    itself = client.post(
        '/api/code-dependencies', headers=HEADERS, json=body | {'to_code_project_id': body['from_code_project_id']}
    )
    assert (itself.status_code, itself.json()['error']['message']) == (400, SELF)

    listed = client.get(f'/api/code-projects/{world["lib"]}/dependencies', headers=HEADERS)
    assert listed.status_code == 200, listed.text
    used_by = listed.json()['used_by']
    assert [d['code_project']['name'] for d in used_by['code_projects']] == ['app']
    assert [s['name'] for s in used_by['services']] == ['api']
    assert client.get(f'/api/code-projects/{world["old"]}/dependencies', headers=HEADERS).status_code == 404
    assert client.get(f'/api/code-projects/{uuid4()}/dependencies', headers=HEADERS).status_code == 404

    with store.connect() as conn:
        draft = depend(conn, world, 'util', 'lib', status='draft')
    url = f'/api/inventory/code_dependency/{draft["id"]}'
    confirmed = client.post(f'{url}/confirm', headers=HEADERS, json={'revision': 1})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {
        'confirmed': [{'kind': 'code_dependency', 'id': str(draft['id']), 'name': 'util depends on lib'}]
    }
    stale = client.delete(f'/api/code-dependencies/{dependency["id"]}', headers=HEADERS, params={'revision': 9})
    assert stale.status_code == 409
    gone = client.delete(f'/api/code-dependencies/{dependency["id"]}', headers=HEADERS, params={'revision': 1})
    assert gone.status_code == 200, gone.text
    listed = client.get(f'/api/code-projects/{world["lib"]}/dependencies', headers=HEADERS).json()
    assert [d['code_project']['name'] for d in listed['used_by']['code_projects']] == ['util']


def test_the_api_discards_a_draft(client, store, world):
    with store.connect() as conn:
        draft = depend(conn, world, 'app', 'lib', status='draft')
    url = f'/api/inventory/code_dependency/{draft["id"]}/discard'
    response = client.post(url, headers=HEADERS, json={'revision': 1})
    assert response.status_code == 200, response.text
    assert client.get(f'/api/code-projects/{world["app"]}/dependencies', headers=HEADERS).json()['depends_on'] == []


# --- the agent tools ---


def entry(type_: str, id_) -> dict:
    return {'type': type_, 'id': str(id_), 'name': type_, 'description_short': '', 'source': 'given'}


def call(config: dict, tool: str, /, **arguments) -> dict | str:
    spec = CATALOG[tool]
    reply = asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))
    try:
        return json.loads(reply)
    except json.JSONDecodeError:
        return reply


@pytest.fixture
def run(store, world, monkeypatch):
    """A session of ops-agent whose access has code projects app and lib (not util)."""
    monkeypatch.setattr(inventory_capability, '_store', store)
    with store.connect() as conn:
        sid = add_chat_session(conn, world['project'], world['actor'], 'ops-agent', a_model(conn, world['actor']))
    access = {'resources': [entry('code_project', world['app']), entry('code_project', world['lib'])]}
    return {'project_id': str(world['project']), 'session_id': str(sid), 'access': access}


def test_the_pure_check_needs_both_code_projects_in_the_access():
    access = {'resources': [entry('code_project', 'c1'), entry('code_project', 'c2')]}
    given = {'kind': 'code_depends_on', 'from_code_project_id': 'c1'}
    assert tools.check_save_relation(access, to_code_project_id='c2', **given) is None
    refused = 'This code project is not available in this run.'
    assert tools.check_save_relation(access, to_code_project_id='c9', **given) == refused
    rows = {('code_dependency', 'd1'): {'from_code_project_id': 'c1', 'to_code_project_id': 'c2', 'status': 'draft'}}
    assert tools.check_remove_inventory_item(access, kind='code_dependency', id='d1', rows=rows) is None
    confirmed = {key: {**row, 'status': 'confirmed'} for key, row in rows.items()}
    assert tools.check_remove_inventory_item(access, kind='code_dependency', id='d1', rows=confirmed) == (
        'This code dependency is confirmed; only a person can change it. Add a new draft instead, or describe the '
        'change in your answer.'
    )
    elsewhere = {key: {**row, 'from_code_project_id': 'c9'} for key, row in rows.items()}
    assert tools.check_remove_inventory_item(access, kind='code_dependency', id='d1', rows=elsewhere) == (
        'This code dependency is not available in this run.'
    )


def test_an_agent_drafts_updates_and_removes_a_code_dependency(store, world, run):
    saved = call(
        run,
        'save_relation',
        kind='code_depends_on',
        from_code_project_id=str(world['app']),
        to_code_project_id=str(world['lib']),
        description='imports lib (pyproject.toml)',
    )
    assert (saved['status'], saved['created_by_agent'], saved['description']) == (
        'draft',
        'ops-agent',
        'imports lib (pyproject.toml)',
    )
    again = call(
        run,
        'save_relation',
        kind='code_depends_on',
        from_code_project_id=str(world['app']),
        to_code_project_id=str(world['lib']),
    )
    assert again == {'refused': duplicate('lib')}
    outside = call(
        run,
        'save_relation',
        kind='code_depends_on',
        from_code_project_id=str(world['app']),
        to_code_project_id=str(world['util']),
    )
    assert outside == {'refused': 'This code project is not available in this run.'}
    itself = call(
        run,
        'save_relation',
        kind='code_depends_on',
        from_code_project_id=str(world['app']),
        to_code_project_id=str(world['app']),
    )
    assert itself == {'refused': SELF}
    updated = call(run, 'save_relation', id=saved['id'], revision=1, kind='code_depends_on', description='http client')
    assert (updated['description'], updated['revision'], updated['status']) == ('http client', 2, 'draft')

    resource = call(run, 'get_resource', resource_type='code_project', resource_id=str(world['app']))
    [link] = [link for link in resource['links'] if link['type'] == 'code_project']
    assert (link['relation'], link['name'], link['status'], link['dependency_id'], link['revision']) == (
        'depends_on',
        'lib',
        'draft',
        saved['id'],
        2,
    )
    library = call(run, 'get_resource', resource_type='code_project', resource_id=str(world['lib']))
    assert [(k['relation'], k['name']) for k in library['links'] if k['type'] == 'code_project'] == [('used_by', 'app')]

    removed = call(run, 'remove_inventory_item', kind='code_dependency', id=saved['id'], revision=2)
    assert removed == {'removed': saved['id']}
    with store.connect() as conn:
        assert inventory.list_code_dependencies(conn, world['project']) == []
        confirmed = depend(conn, world, 'app', 'lib')
    refused = call(run, 'remove_inventory_item', kind='code_dependency', id=str(confirmed['id']), revision=1)
    assert 'confirmed; only a person can change it' in refused['refused']


# --- the map ---


def test_the_services_view_draws_depends_on_edges_between_code_projects(client, store, world):
    with store.connect() as conn:
        confirmed = depend(conn, world, 'app', 'lib')
        draft = depend(conn, world, 'util', 'lib', status='draft')
        conn.execute('UPDATE ka.repositories SET archived_at=NULL WHERE id=%s', (world['old'],))
        depend(conn, world, 'old', 'lib')
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (world['old'],))  # not drawn
    response = client.get('/api/map/view/services', headers=HEADERS, params={'depth': 1})
    assert response.status_code == 200, response.text
    edges = [e for e in response.json()['edges'] if e['type'] == 'depends_on']
    found = {(e['from'], e['to'], tuple(e['ids']), e.get('draft')) for e in edges}
    assert found == {
        (f'code_project:{world["app"]}', f'code_project:{world["lib"]}', (str(confirmed['id']),), None),
        (f'code_project:{world["util"]}', f'code_project:{world["lib"]}', (str(draft['id']),), True),
    }
