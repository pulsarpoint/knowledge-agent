"""The first Jira user mapping creates the shared `Jira assignee agents` handler; the mapping list says which handler
runs the mapped agents; Create handler makes it on demand."""

import threading
import time

from knowledge_admin.events import templates as event_templates
from knowledge_admin.jira import api as jira_api
from test_jira_user_agents import setup
from test_repositories import HEADERS

NAME = 'Jira assignee agents'


def handlers(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.event_handlers ORDER BY created_at,id').fetchall()


def created_audits(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute(
            "SELECT * FROM ka.audit_events WHERE action='event_handler.created' ORDER BY created_at"
        ).fetchall()


def map_user(client, key: str, agent: str = 'ops-agent'):
    response = client.put(f'/api/jira/user-agents/{key}', headers=HEADERS, json={'agent_id': agent})
    assert response.status_code == 200, response.text
    return response.json()


def add_handler(client, name: str, agent_from: str = 'assignee') -> dict:
    body = {
        'name': name,
        'type_pattern': 'jira.ticket.*',
        'action': 'agent',
        'agent_from': agent_from,
        'agent_id': None if agent_from == 'assignee' else 'ops-agent',
        'prompt': 'Work on {{ event.subject }}',
    }
    response = client.post('/api/event-handlers', headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    return response.json()['handler']


def test_the_first_mapping_creates_the_handler(store, tmp_path):
    client, _ = setup(store, tmp_path)
    saved = map_user(client, 'JIRAUSER10')
    [handler] = handlers(store)
    assert {
        key: handler[key]
        for key in (
            'name',
            'enabled',
            'type_pattern',
            'conditions',
            'action',
            'emit',
            'agent_from',
            'agent_id',
            'reply_to_jira',
            'give_back',
        )
    } == {
        'name': NAME,
        'enabled': True,
        'type_pattern': 'jira.ticket.*',
        'conditions': [{'field': 'payload.for_assignee', 'op': 'equals', 'value': 'true'}],
        'action': 'agent',
        'emit': None,
        'agent_from': 'assignee',
        'agent_id': None,
        'reply_to_jira': True,
        'give_back': True,
    }
    assert handler['prompt'] == jira_api.ASSIGNEE_PROMPT
    with store.connect() as conn:
        mapping = conn.execute('SELECT created_by FROM ka.jira_user_agents').fetchone()
    assert handler['created_by'] == mapping['created_by']
    [entry] = created_audits(store)
    assert entry['resource_type'] == 'event_handler' and entry['resource_id'] == handler['id']
    assert entry['actor_id'] == handler['created_by'] and entry['details']['automatic'] is True
    assert saved['handler'] == {'id': str(handler['id']), 'name': NAME, 'enabled': True}


def test_later_mappings_reuse_it_and_deletes_leave_it(store, tmp_path):
    client, _ = setup(store, tmp_path)
    map_user(client, 'JIRAUSER10')
    map_user(client, 'ana', 'docs-agent')
    map_user(client, 'ana', 'ops-agent')
    [handler] = handlers(store)
    assert client.delete('/api/jira/user-agents/ana', headers=HEADERS).status_code == 204
    assert client.delete('/api/jira/user-agents/JIRAUSER10', headers=HEADERS).status_code == 204
    assert [row['id'] for row in handlers(store)] == [handler['id']]
    assert len(created_audits(store)) == 1
    listed = client.get('/api/jira/user-agents', headers=HEADERS).json()
    assert listed == {'user_agents': [], 'handler': {'id': str(handler['id']), 'name': NAME, 'enabled': True}}


def test_an_edited_handler_is_not_overwritten(store, tmp_path):
    client, _ = setup(store, tmp_path)
    map_user(client, 'JIRAUSER10')
    with store.connect() as conn:
        conn.execute("UPDATE ka.event_handlers SET name='Renamed',enabled=false,prompt='Mine'")
    map_user(client, 'ana')
    [handler] = handlers(store)
    assert (handler['name'], handler['enabled'], handler['prompt']) == ('Renamed', False, 'Mine')
    listed = client.get('/api/jira/user-agents', headers=HEADERS).json()
    assert listed['handler'] == {'id': str(handler['id']), 'name': 'Renamed', 'enabled': False}


def test_not_created_when_an_assignee_handler_exists(store, tmp_path):
    client, _ = setup(store, tmp_path)
    mine = add_handler(client, 'My assignee handler')
    saved = map_user(client, 'JIRAUSER10')
    assert [row['name'] for row in handlers(store)] == ['My assignee handler']
    assert saved['handler'] == {'id': mine['id'], 'name': 'My assignee handler', 'enabled': True}


def test_a_fixed_agent_handler_does_not_count(store, tmp_path):
    client, _ = setup(store, tmp_path)
    add_handler(client, 'Fixed', agent_from='fixed')
    assert client.get('/api/jira/user-agents', headers=HEADERS).json()['handler'] is None
    map_user(client, 'JIRAUSER10')
    assert sorted(row['name'] for row in handlers(store)) == ['Fixed', NAME]


def test_a_taken_name_still_saves_the_mapping(store, tmp_path):
    client, _ = setup(store, tmp_path)
    add_handler(client, NAME, agent_from='fixed')
    saved = map_user(client, 'JIRAUSER10')
    assert [row['jira_user_key'] for row in saved['user_agents']] == ['JIRAUSER10']
    assert saved['handler'] is None and len(handlers(store)) == 1


def test_concurrent_first_saves_create_one_handler(store, tmp_path):
    client, _ = setup(store, tmp_path)
    with store.connect() as first:
        project = store.project(first, 'shop')
        actor = store.actor(first, 'someone', 'someone@example.com')
        jira_api.create_assignee_handler(first, project, actor)  # not committed yet
        result = {}
        second = threading.Thread(target=lambda: result.update(response=map_user(client, 'ana')))
        second.start()
        with store.connect() as watcher:
            for _ in range(200):  # wait until the second save waits on the first's handler
                waiting = watcher.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type='Lock' AND datname=current_database()"
                ).fetchone()['n']
                if waiting:
                    break
                time.sleep(0.02)
        assert waiting
    second.join(10)
    assert [row['name'] for row in handlers(store)] == [NAME]
    assert result['response']['handler']['name'] == NAME
    assert [row['jira_user_key'] for row in result['response']['user_agents']] == ['ana']


def test_the_list_without_a_handler(store, tmp_path):
    client, _ = setup(store, tmp_path)
    assert client.get('/api/jira/user-agents', headers=HEADERS).json() == {'user_agents': [], 'handler': None}


def test_create_handler_on_demand(store, tmp_path):
    client, _ = setup(store, tmp_path)
    response = client.post('/api/jira/user-agents/handler', headers=HEADERS)
    assert response.status_code == 201, response.text
    [handler] = handlers(store)
    assert (handler['name'], handler['agent_from'], handler['give_back']) == (NAME, 'assignee', True)
    assert response.json() == {'handler': {'id': str(handler['id']), 'name': NAME, 'enabled': True}}
    assert created_audits(store)[0]['details']['automatic'] is True
    again = client.post('/api/jira/user-agents/handler', headers=HEADERS)
    assert again.status_code == 409
    assert len(handlers(store)) == 1


def test_create_handler_when_the_name_is_taken(store, tmp_path):
    client, _ = setup(store, tmp_path)
    add_handler(client, NAME, agent_from='fixed')
    response = client.post('/api/jira/user-agents/handler', headers=HEADERS)
    assert response.status_code == 409
    assert response.json()['error']['message'] == 'A handler with this name already exists.'


def test_the_default_prompt_renders_a_ticket():
    event = {
        'id': 1,
        'source': 'jira',
        'type': 'jira.ticket.updated',
        'subject': 'DEMO-1',
        'title': 'Printer on fire',
        'occurred_at': None,
        'payload': {
            'key': 'DEMO-1',
            'summary': 'Printer on fire',
            'description': 'Smoke everywhere.',
            'status': 'Open',
            'reporter': 'Ana Petrović',
            'comments': [{'id': '1', 'author': 'Marko', 'created': '2026-10-20', 'body': 'Still burning.'}],
        },
    }
    prompt = event_templates.render_prompt(jira_api.ASSIGNEE_PROMPT, event)
    for text in ('DEMO-1', 'Printer on fire', 'Smoke everywhere.', 'Open', 'Ana Petrović', 'Marko', 'Still burning.'):
        assert text in prompt
    bare = {**event, 'payload': {'key': 'DEMO-2', 'summary': 'S', 'description': '', 'status': None, 'reporter': None}}
    assert 'DEMO-2' in event_templates.render_prompt(jira_api.ASSIGNEE_PROMPT, bare)
