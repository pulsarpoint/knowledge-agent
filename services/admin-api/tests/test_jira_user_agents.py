"""Jira users → agents: the mapping rows under the primary connection and the user search."""

import httpx

from knowledge_admin.jira.client import JiraClient
from test_api import _client
from test_jira import SETTINGS
from test_repositories import HEADERS

JIRA_USERS = [
    {
        'key': 'JIRAUSER10',
        'name': 'ops-bot',
        'displayName': 'Ops Bot',
        'emailAddress': 'ops@example.com',
        'active': True,
        'avatarUrls': {'48x48': 'https://jira.internal/avatar'},
    },
    {'key': 'ana', 'name': 'ana', 'displayName': 'Ana Petrović', 'emailAddress': 'ana@example.com', 'active': True},
    {'key': 'gone', 'name': 'gone', 'displayName': 'Gone User', 'active': False},
]


def jira_users(requests: list[httpx.Request], status: int = 200):
    def handler(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer private-jira-token'
        assert request.url.host == 'jira.internal'
        assert request.url.path == '/jira/rest/api/2/user/search'
        if status != 200:
            return httpx.Response(status, text='secret echoed: private-jira-token')
        return httpx.Response(200, json=JIRA_USERS)

    return handler


def setup(store, tmp_path, handler=None):
    client = _client(tmp_path, repository_store=store, jira_transport=httpx.MockTransport(handler or jira_users([])))
    response = client.put('/api/jira/settings', headers=HEADERS, json=SETTINGS)
    assert response.status_code == 200, response.text
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.agents(id,name,enabled,system_prompt) VALUES ('ops-agent','Ops agent',true,''),
            ('docs-agent','Docs agent',false,'')"""
        )
    return client, response.json()['connection']


def add_ticket(store, connection_id, number: int, assignee_id: str | None, assignee_name: str | None):
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.jira_tickets(project_id,connection_id,remote_id,issue_key,jira_project_key,summary,
            assignee_id,assignee_name,raw_payload,source_updated_at)
            SELECT project_id,id,%s,%s,'DEMO','Ticket',%s,%s,'{}',now() FROM ka.jira_connections WHERE id=%s""",
            (str(number), f'DEMO-{number}', assignee_id, assignee_name, connection_id),
        )


def test_put_lists_changes_and_deletes_mappings(store, tmp_path):
    client, _ = setup(store, tmp_path)
    assert client.get('/api/jira/user-agents', headers=HEADERS).json() == {'user_agents': [], 'handler': None}
    saved = client.put(
        '/api/jira/user-agents/JIRAUSER10', headers=HEADERS, json={'name': 'Ops Bot', 'agent_id': 'ops-agent'}
    )
    assert saved.status_code == 200, saved.text
    client.put('/api/jira/user-agents/ana', headers=HEADERS, json={'name': 'Ana Petrović', 'agent_id': 'docs-agent'})
    rows = client.get('/api/jira/user-agents', headers=HEADERS).json()['user_agents']
    assert [(r['jira_user_key'], r['jira_user_name'], r['agent_id'], r['agent_name'], r['enabled']) for r in rows] == [
        ('ana', 'Ana Petrović', 'docs-agent', 'Docs agent', False),
        ('JIRAUSER10', 'Ops Bot', 'ops-agent', 'Ops agent', True),
    ]
    # One Jira user maps to one agent: a second put changes the row.
    changed = client.put('/api/jira/user-agents/ana', headers=HEADERS, json={'name': 'Ana P.', 'agent_id': 'ops-agent'})
    assert changed.status_code == 200
    rows = client.get('/api/jira/user-agents', headers=HEADERS).json()['user_agents']
    assert [(r['jira_user_key'], r['jira_user_name'], r['agent_id']) for r in rows] == [
        ('ana', 'Ana P.', 'ops-agent'),
        ('JIRAUSER10', 'Ops Bot', 'ops-agent'),
    ]
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.jira_user_agents').fetchone()['n'] == 2
        row = conn.execute("SELECT * FROM ka.jira_user_agents WHERE jira_user_key='ana'").fetchone()
        assert (
            row['created_by'] is not None and row['updated_by'] is not None and row['updated_at'] >= row['created_at']
        )
        actions = [
            r['action']
            for r in conn.execute(
                "SELECT action FROM ka.audit_events WHERE resource_type='jira_user_agent' ORDER BY created_at"
            )
        ]
    assert actions == ['jira.user_agent_saved'] * 3
    assert client.delete('/api/jira/user-agents/ana', headers=HEADERS).status_code == 204
    assert client.delete('/api/jira/user-agents/ana', headers=HEADERS).status_code == 404
    rows = client.get('/api/jira/user-agents', headers=HEADERS).json()['user_agents']
    assert [r['jira_user_key'] for r in rows] == ['JIRAUSER10']


def test_a_typed_key_without_a_name_shows_the_key(store, tmp_path):
    client, _ = setup(store, tmp_path)
    assert client.put('/api/jira/user-agents/marko', headers=HEADERS, json={'agent_id': 'ops-agent'}).status_code == 200
    rows = client.get('/api/jira/user-agents', headers=HEADERS).json()['user_agents']
    assert rows[0]['jira_user_name'] == 'marko'


def test_put_validates_agent_key_and_name(store, tmp_path):
    client, _ = setup(store, tmp_path)
    missing = client.put('/api/jira/user-agents/ana', headers=HEADERS, json={'agent_id': 'nobody'})
    assert missing.status_code == 400 and missing.json()['error']['message'] == 'Choose an existing agent'
    long_key = client.put(f'/api/jira/user-agents/{"k" * 256}', headers=HEADERS, json={'agent_id': 'ops-agent'})
    assert long_key.status_code == 400
    blank_key = client.put('/api/jira/user-agents/%20', headers=HEADERS, json={'agent_id': 'ops-agent'})
    assert blank_key.status_code == 400
    long_name = client.put(
        '/api/jira/user-agents/ana', headers=HEADERS, json={'agent_id': 'ops-agent', 'name': 'n' * 256}
    )
    assert long_name.status_code == 400
    assert (
        client.put(f'/api/jira/user-agents/{"k" * 255}', headers=HEADERS, json={'agent_id': 'ops-agent'}).status_code
        == 200
    )


def test_without_a_connection_there_are_no_mappings_to_change(store, tmp_path):
    client = _client(tmp_path, repository_store=store, jira_transport=httpx.MockTransport(jira_users([])))
    assert client.get('/api/jira/user-agents', headers=HEADERS).json() == {'user_agents': [], 'handler': None}
    response = client.put('/api/jira/user-agents/ana', headers=HEADERS, json={'agent_id': 'search'})
    assert response.status_code == 400


def test_a_key_is_unique_per_connection(store, tmp_path):
    client, config = setup(store, tmp_path)
    client.put('/api/jira/user-agents/ana', headers=HEADERS, json={'agent_id': 'ops-agent'})
    with store.connect() as conn:
        try:
            conn.execute(
                """INSERT INTO ka.jira_user_agents(connection_id,jira_user_key,jira_user_name,agent_id)
                VALUES (%s,'ana','Ana','docs-agent')""",
                (config['id'],),
            )
            raised = False
        except Exception as error:  # noqa: BLE001 - the unique violation
            raised = 'jira_user_agents' in str(error)
    assert raised


def test_deleting_an_agent_removes_only_its_mappings(store, tmp_path):
    client, _ = setup(store, tmp_path)
    client.put('/api/jira/user-agents/ana', headers=HEADERS, json={'agent_id': 'ops-agent'})
    client.put('/api/jira/user-agents/marko', headers=HEADERS, json={'agent_id': 'docs-agent'})
    with store.connect() as conn:
        conn.execute("DELETE FROM ka.agents WHERE id='ops-agent'")
    rows = client.get('/api/jira/user-agents', headers=HEADERS).json()['user_agents']
    assert [r['jira_user_key'] for r in rows] == ['marko']


def test_deleting_the_connection_removes_its_mappings(store, tmp_path):
    client, config = setup(store, tmp_path)
    client.put('/api/jira/user-agents/ana', headers=HEADERS, json={'agent_id': 'ops-agent'})
    with store.connect() as conn:
        conn.execute('DELETE FROM ka.jira_connections WHERE id=%s', (config['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.jira_user_agents').fetchone()['n'] == 0


def test_user_search_merges_jira_users_with_seen_assignees(store, tmp_path):
    requests: list[httpx.Request] = []
    client, config = setup(store, tmp_path, jira_users(requests))
    add_ticket(store, config['id'], 1, 'ana', 'Ana (old name)')
    add_ticket(store, config['id'], 2, 'marko', 'Marko Ilić')
    add_ticket(store, config['id'], 3, 'marko', 'Marko Ilić')
    add_ticket(store, config['id'], 4, 'other', 'Someone Else')
    add_ticket(store, config['id'], 5, None, None)
    response = client.get('/api/jira/users', headers=HEADERS, params={'query': 'a'})
    assert response.status_code == 200, response.text
    assert response.json() == {
        'users': [
            {'key': 'JIRAUSER10', 'name': 'ops-bot', 'display_name': 'Ops Bot'},
            {'key': 'ana', 'name': 'ana', 'display_name': 'Ana Petrović'},
            {'key': 'marko', 'name': None, 'display_name': 'Marko Ilić'},
        ],
        'warning': None,
    }
    assert [request.url.params['username'] for request in requests] == ['a']
    for hidden in ['example.com', 'avatar', 'active', 'private-jira-token', 'Gone User', 'Someone Else']:
        assert hidden not in response.text


def test_user_search_when_jira_fails_returns_seen_assignees_and_a_warning(store, tmp_path):
    client, config = setup(store, tmp_path, jira_users([], status=500))
    add_ticket(store, config['id'], 1, 'marko', 'Marko Ilić')
    response = client.get('/api/jira/users', headers=HEADERS, params={'query': 'mark'})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['users'] == [{'key': 'marko', 'name': None, 'display_name': 'Marko Ilić'}]
    assert data['warning'] and 'private-jira-token' not in response.text and len(data['warning']) < 200


def test_user_search_needs_a_query(store, tmp_path):
    client, _ = setup(store, tmp_path)
    assert client.get('/api/jira/users', headers=HEADERS).status_code == 400
    assert client.get('/api/jira/users', headers=HEADERS, params={'query': ' '}).status_code == 400


def test_client_search_users_returns_display_data_only():
    requests: list[httpx.Request] = []
    with JiraClient(
        'https://jira.internal/jira', 'private-jira-token', httpx.MockTransport(jira_users(requests))
    ) as jira:
        users = jira.search_users('a')
    assert users == [
        {'key': 'JIRAUSER10', 'name': 'ops-bot', 'display_name': 'Ops Bot'},
        {'key': 'ana', 'name': 'ana', 'display_name': 'Ana Petrović'},
    ]
    assert requests[0].url.params['username'] == 'a'
