"""Administration → Events → Handlers: create, list, edit with revisions, delete, Preview against a real event."""

import pytest
from psycopg.types.json import Jsonb

from test_api import _client
from test_events_api import seed
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)

EMIT = {
    'name': 'Triage',
    'enabled': True,
    'type_pattern': 'jira.ticket.*',
    'conditions': [{'field': 'payload.labels', 'op': 'contains', 'value': 'agent'}],
    'action': 'emit',
    'emit': {'type': 'triage.requested', 'subject': '', 'title': 'Triage {{ event.subject }}', 'payload': ''},
}


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def create(client, **changes):
    response = client.post('/api/event-handlers', headers=HEADERS, json={**EMIT, **changes})
    assert response.status_code == 201, response.text
    return response.json()['handler']


def error(response, status):
    assert response.status_code == status, response.text
    return response.json()['error']['message']


def test_create_list_edit_delete(client, store):
    handler = create(client)
    assert (handler['name'], handler['revision'], handler['agent_id'], handler['prompt'], handler['last_run']) == (
        'Triage',
        1,
        None,
        None,
        None,
    )
    assert handler['conditions'] == [{'field': 'payload.labels', 'op': 'contains', 'value': 'agent'}]
    assert client.get('/api/event-handlers', headers=HEADERS).json()['handlers'] == [handler]
    saved = client.put(
        f'/api/event-handlers/{handler["id"]}', headers=HEADERS, json={**EMIT, 'enabled': False, 'revision': 1}
    ).json()['handler']
    assert (saved['enabled'], saved['revision']) == (False, 2)
    assert (
        error(client.put(f'/api/event-handlers/{handler["id"]}', headers=HEADERS, json={**EMIT, 'revision': 1}), 409)
        == 'This handler was changed by someone else. Reload and try again.'
    )
    assert (
        error(client.post('/api/event-handlers', headers=HEADERS, json=EMIT), 409)
        == 'A handler with this name already exists.'
    )
    assert client.delete(f'/api/event-handlers/{handler["id"]}', headers=HEADERS).status_code == 204
    assert error(client.delete(f'/api/event-handlers/{handler["id"]}', headers=HEADERS), 404) == 'Handler not found.'
    with store.connect() as conn:
        actions = [
            row['action']
            for row in conn.execute(
                "SELECT action FROM ka.audit_events WHERE resource_type='event_handler' ORDER BY id"
            )
        ]
    assert actions == ['event_handler.created', 'event_handler.updated', 'event_handler.deleted']


def test_last_run_is_listed(client, store):
    handler = create(client)
    [event_id] = seed(store, 1)
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message)
            VALUES (%s,%s,'Triage','failed','x')""",
            (event_id, handler['id']),
        )
    [listed] = client.get('/api/event-handlers', headers=HEADERS).json()['handlers']
    assert listed['last_run']['status'] == 'failed'


@pytest.mark.parametrize(
    'changes,message',
    [
        ({'type_pattern': 'Jira.*'}, 'Use lowercase dotted names'),
        ({'conditions': [{'field': 'labels', 'op': 'equals', 'value': 'x'}]}, 'Condition 1: use source'),
        ({'conditions': [{'field': 'title', 'op': 'in', 'value': 'x'}]}, 'Condition 1: list one or more values'),
        ({'emit': None}, 'New event type: enter a type'),
        ({'emit': {'type': 'a.b', 'subject': '{{ event.subject'}}, 'Subject: '),
        ({'action': 'agent', 'emit': None}, 'Choose an agent.'),
        ({'action': 'agent', 'emit': None, 'agent_id': 'nope', 'prompt': 'Hi'}, 'Agent not found.'),
        ({'name': ' '}, 'Enter a name.'),
        ({'name': 'A\x00'}, 'Remove the NUL character from Name.'),
        ({'type_pattern': 'jira.\x00'}, 'Remove the NUL character from Event type.'),
        (
            {'conditions': [{'field': 'title', 'op': 'equals', 'value': 'x\x00'}]},
            'Remove the NUL character from Conditions.',
        ),
        ({'emit': {'type': 'a.b', 'title': '\x00'}}, 'Remove the NUL character from Title.'),
        (
            {'action': 'agent', 'emit': None, 'agent_id': 'x', 'prompt': 'Hi\x00'},
            'Remove the NUL character from Prompt.',
        ),
        (
            {'action': 'agent', 'emit': None, 'agent_id': 'x\x00', 'prompt': 'Hi'},
            'Remove the NUL character from Agent.',
        ),
    ],
)
def test_validation(client, changes, message):
    assert error(client.post('/api/event-handlers', headers=HEADERS, json={**EMIT, **changes}), 400).startswith(message)


def test_send_to_agent_handlers_and_the_agent_list(tester):
    t = tester
    listed = t.client.get('/api/event-handlers/agents', headers=HEADERS).json()['agents']
    assert {'id': t.agent, 'name': 'Tester'} in listed  # seeded agents (0020, 0031) may be listed too
    with t.store.connect() as conn:  # the database's own collation, as the picker shows them
        expected = [
            row['id']
            for row in conn.execute("SELECT id FROM ka.agents WHERE enabled AND resources='fixed' ORDER BY name,id")
        ]
    assert [agent['id'] for agent in listed] == expected
    body = {
        **EMIT,
        'action': 'agent',
        'emit': {'type': 'ignored.type'},
        'agent_id': t.agent,
        'prompt': 'Triage {{ event.subject }}',
    }
    response = t.client.post('/api/event-handlers', headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    handler = response.json()['handler']
    assert (handler['emit'], handler['agent_id'], handler['agent_name'], handler['prompt']) == (
        None,
        t.agent,
        'Tester',
        'Triage {{ event.subject }}',
    )
    assert (
        error(t.client.post('/api/event-handlers', headers=HEADERS, json={**body, 'name': 'B', 'prompt': ' '}), 400)
        == 'Prompt: write what the agent should do.'
    )
    with t.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id=%s", (t.agent,))
    assert t.agent not in [
        agent['id'] for agent in t.client.get('/api/event-handlers/agents', headers=HEADERS).json()['agents']
    ]
    assert 'resources per run' in error(
        t.client.post('/api/event-handlers', headers=HEADERS, json={**body, 'name': 'C'}), 400
    )


def test_preview(client, store):
    [plain] = seed(store, 1)
    [labelled] = seed(store, 1, subject='DEMO-9', payload=Jsonb({'labels': ['agent'], 'changes': {}}))
    miss = client.post('/api/event-handlers/preview', headers=HEADERS, json={'handler': EMIT, 'event_id': plain}).json()
    assert miss == {
        'matches': False,
        'reason': 'payload.labels is missing; it must contain "agent"',
        'output': {
            'type': 'triage.requested',
            'subject': 'DEMO-1',
            'title': 'Triage DEMO-1',
            'payload': {'issue_id': '1', 'changes': {}},
        },
    }
    hit = client.post(
        '/api/event-handlers/preview', headers=HEADERS, json={'handler': EMIT, 'event_id': labelled}
    ).json()
    assert (hit['matches'], hit['reason'], hit['output']['subject']) == (True, None, 'DEMO-9')
    broken = {**EMIT, 'name': '', 'emit': {'type': '{{ event.payload.nope }}'}}
    assert (
        client.post('/api/event-handlers/preview', headers=HEADERS, json={'handler': broken, 'event_id': labelled})
        .json()['output']['error']
        .startswith('Template error:')
    )
    agent = {**EMIT, 'action': 'agent', 'emit': None, 'agent_id': 'x', 'prompt': 'Do {{ event.subject }}'}
    assert client.post(
        '/api/event-handlers/preview', headers=HEADERS, json={'handler': agent, 'event_id': labelled}
    ).json()['output'] == {'prompt': 'Do DEMO-9'}
    assert (
        error(
            client.post('/api/event-handlers/preview', headers=HEADERS, json={'handler': EMIT, 'event_id': 999999}), 404
        )
        == 'Event not found.'
    )


def test_a_handler_whose_agent_is_gone_can_still_be_turned_off_and_edited(tester):
    t = tester
    body = {**EMIT, 'action': 'agent', 'emit': None, 'agent_id': t.agent, 'prompt': 'Triage {{ event.subject }}'}
    handler = t.client.post('/api/event-handlers', headers=HEADERS, json=body).json()['handler']
    with t.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id=%s", (t.agent,))
    url = f'/api/event-handlers/{handler["id"]}'
    saved = t.client.put(url, headers=HEADERS, json={**body, 'enabled': False, 'revision': 1})
    assert saved.status_code == 200, saved.text
    assert saved.json()['handler']['enabled'] is False
    assert 'resources per run' in error(
        t.client.put(url, headers=HEADERS, json={**body, 'revision': 2}), 400
    )  # turning it on checks
    revision = t.client.get(f'/api/agents/{t.agent}', headers=HEADERS).json()['revision']
    deleted = t.client.request(
        'DELETE',
        f'/api/agents/{t.agent}',
        headers=HEADERS,
        json={'revision': revision, 'confirmed': True, 'with_history': True},
    )
    assert deleted.status_code == 204, deleted.text
    edited = t.client.put(
        url, headers=HEADERS, json={**body, 'enabled': False, 'prompt': 'Later {{ event.subject }}', 'revision': 2}
    )
    assert edited.status_code == 200, edited.text
    assert (edited.json()['handler']['agent_id'], edited.json()['handler']['agent_name']) == (t.agent, None)
    assert (
        error(
            t.client.put(url, headers=HEADERS, json={**body, 'enabled': False, 'agent_id': 'missing', 'revision': 3}),
            400,
        )
        == 'Agent not found.'
    )
