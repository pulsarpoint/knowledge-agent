"""Administration → Events: newest first, filters, cursor paging, one event with its payload."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS


def seed(store, count=5, **values) -> list[int]:
    ids = []
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for number in range(1, count + 1):
            row = {'project_id': project, 'source': 'jira', 'source_id': uuid4(), 'type': 'jira.ticket.created',
                   'subject': f'DEMO-{number}', 'title': f'Ticket {number}', 'payload': Jsonb({'issue_id': str(number), 'changes': {}}),
                   'dedupe_key': f'k{uuid4()}', 'occurred_at': '2026-10-09T10:00:00Z', **values}
            ids.append(conn.execute(f"INSERT INTO ka.events({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING id",
                                    tuple(row.values())).fetchone()['id'])
    return ids


def test_newest_first_with_cursor(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, 5)
    page = client.get('/api/events', headers=HEADERS, params={'limit': 2}).json()
    assert [e['id'] for e in page['events']] == ids[::-1][:2] and page['next_before'] == ids[3]
    rest = client.get('/api/events', headers=HEADERS, params={'limit': 10, 'before': page['next_before']}).json()
    assert [e['id'] for e in rest['events']] == ids[::-1][2:] and rest['next_before'] is None
    assert 'payload' not in page['events'][0]


def test_filters_and_search(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store, 2)
    seed(store, 1, type='jira.ticket.updated', subject='OPS-7', title='Disk 100% full_')
    seed(store, 1, subject='XYZ-1', title='xyz')
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'type': 'jira.ticket.updated'}).json()['events']] == ['OPS-7']
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': 'ops-'}).json()['events']] == ['OPS-7']
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': '100%'}).json()['events']] == ['OPS-7']
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': '%'}).json()['events']] == ['OPS-7']
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': '_'}).json()['events']] == ['OPS-7']
    assert client.get('/api/events', headers=HEADERS, params={'source': 'jira'}).json()['events'] != []
    assert client.get('/api/events/facets', headers=HEADERS).json() == {'sources': ['jira'], 'types': ['jira.ticket.created', 'jira.ticket.updated']}
    assert client.get('/api/events', headers=HEADERS, params={'limit': 201}).status_code == 400


def test_one_event_and_missing(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [event_id] = seed(store, 1)
    event = client.get(f'/api/events/{event_id}', headers=HEADERS).json()['event']
    assert event['payload'] == {'issue_id': '1', 'changes': {}} and event['dedupe_key'].startswith('k')
    missing = client.get('/api/events/999999', headers=HEADERS)
    assert missing.status_code == 404 and missing.json()['error']['message'] == 'Event not found.'


def test_other_projects_events_are_not_listed(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other-x','Other') RETURNING id").fetchone()['id']
    [foreign] = seed(store, 1, project_id=other)
    assert client.get('/api/events', headers=HEADERS).json()['events'] == []
    assert client.get(f'/api/events/{foreign}', headers=HEADERS).status_code == 404
