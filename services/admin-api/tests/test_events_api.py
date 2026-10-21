"""Administration → Events: newest first, filters, offset paging, one event with its payload."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS


def seed(store, count=5, **values) -> list[int]:
    ids = []
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for number in range(1, count + 1):
            row = {
                'project_id': project,
                'source': 'jira',
                'source_id': uuid4(),
                'type': 'jira.ticket.created',
                'subject': f'DEMO-{number}',
                'title': f'Ticket {number}',
                'payload': Jsonb({'issue_id': str(number), 'changes': {}}),
                'dedupe_key': f'k{uuid4()}',
                'occurred_at': '2026-10-09T10:00:00Z',
                **values,
            }
            ids.append(
                conn.execute(
                    f'INSERT INTO ka.events({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING id',
                    tuple(row.values()),
                ).fetchone()['id']
            )
    return ids


def test_newest_first_with_offsets(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, 5)
    page = client.get('/api/events', headers=HEADERS, params={'limit': 2}).json()
    assert [e['id'] for e in page['events']] == ids[::-1][:2] and page['next_offset'] == 2
    rest = client.get('/api/events', headers=HEADERS, params={'limit': 10, 'offset': 2}).json()
    assert [e['id'] for e in rest['events']] == ids[::-1][2:] and rest['next_offset'] is None
    assert 'payload' not in page['events'][0]
    assert {'source', 'status', 'depth', 'parent_id', 'root_id', 'handler_id'} <= set(page['events'][0])


def test_sorting_ties_page_by_id_without_repeats(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, 5, subject='SAME-1')
    seen = []
    for offset in (0, 2, 4):
        page = client.get(
            '/api/events', headers=HEADERS, params={'sort': 'subject', 'order': 'asc', 'limit': 2, 'offset': offset}
        ).json()
        seen += [e['id'] for e in page['events']]
    assert seen == ids  # ties ordered by id in the same direction, no repeats, no gaps
    newest = client.get('/api/events', headers=HEADERS, params={'sort': 'subject', 'order': 'desc'}).json()['events']
    assert [e['id'] for e in newest] == ids[::-1]
    assert client.get('/api/events', headers=HEADERS, params={'sort': 'payload'}).status_code == 400
    assert client.get('/api/events', headers=HEADERS, params={'offset': -1}).status_code == 400


def test_sort_by_source_and_status_and_filters(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [root] = seed(store, 1, subject='A-1')
    handler = uuid4()
    [child] = seed(store, 1, subject='B-1', source='handler', handler_id=handler, parent_id=root, root_id=root, depth=1)
    with store.connect() as conn:
        conn.execute("UPDATE ka.events SET status='failed',processed_at=now() WHERE id=%s", (root,))
    by_source = client.get('/api/events', headers=HEADERS, params={'sort': 'source', 'order': 'asc'}).json()['events']
    assert [e['source'] for e in by_source] == ['handler', 'jira']
    by_status = client.get('/api/events', headers=HEADERS, params={'sort': 'status', 'order': 'asc'}).json()['events']
    assert [e['status'] for e in by_status] == ['failed', 'pending']
    assert [
        e['id'] for e in client.get('/api/events', headers=HEADERS, params={'status': 'failed'}).json()['events']
    ] == [root]
    assert [
        e['id']
        for e in client.get('/api/events', headers=HEADERS, params={'handler_id': str(handler)}).json()['events']
    ] == [child]
    assert [e['id'] for e in client.get('/api/events', headers=HEADERS, params={'root_id': root}).json()['events']] == [
        child,
        root,
    ]
    assert client.get('/api/events', headers=HEADERS, params={'status': 'done'}).status_code == 400


def test_facets(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store, 1)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        handler = conn.execute(
            """INSERT INTO ka.event_handlers(project_id,name,type_pattern,action,emit,created_by)
            VALUES (%s,'Triage','*','emit',%s,%s) RETURNING id""",
            (project, Jsonb({'type': 'a.b'}), actor),
        ).fetchone()['id']
    assert client.get('/api/events/facets', headers=HEADERS).json() == {
        'sources': ['jira'],
        'types': [
            'jira.ticket.created',
            'jira.ticket.updated',
            'memory.deleted',
            'memory.restored',
            'memory.saved',
            'memory.updated',
            'schedule.fired',
        ],
        'statuses': ['pending'],
        'handlers': [{'id': str(handler), 'name': 'Triage'}],
        'schedules': [],
    }


def test_one_event_has_its_parent_chain_and_runs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [root] = seed(store, 1)
    handler = uuid4()
    [child] = seed(
        store,
        1,
        subject='T-1',
        type='triage.requested',
        source='handler',
        handler_id=handler,
        parent_id=root,
        root_id=root,
        depth=1,
    )
    [grandchild] = seed(
        store,
        1,
        subject='T-2',
        type='triage.done',
        source='handler',
        handler_id=handler,
        parent_id=child,
        root_id=root,
        depth=2,
    )
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,emitted_event_id)
            VALUES (%s,%s,'Triage','succeeded','Emitted triage.done.',%s)""",
            (child, handler, grandchild),
        )
    event = client.get(f'/api/events/{child}', headers=HEADERS).json()['event']
    assert event['parent']['id'] == root and 'payload' not in event['parent']
    assert [(e['id'], e['depth']) for e in event['chain']] == [(root, 0), (child, 1), (grandchild, 2)]
    assert [
        (r['handler_name'], r['status'], r['emitted_event_id'], r['agent_session_id'], r['agent_id'], r['agent_task'])
        for r in event['runs']
    ] == [('Triage', 'succeeded', grandchild, None, None, None)]
    assert client.get(f'/api/events/{root}', headers=HEADERS).json()['event']['parent'] is None


def test_filters_and_search(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store, 2)
    seed(store, 1, type='jira.ticket.updated', subject='OPS-7', title='Disk 100% full_')
    seed(store, 1, subject='XYZ-1', title='xyz')
    assert [
        e['subject']
        for e in client.get('/api/events', headers=HEADERS, params={'type': 'jira.ticket.updated'}).json()['events']
    ] == ['OPS-7']
    assert [
        e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': 'ops-'}).json()['events']
    ] == ['OPS-7']
    assert [
        e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': '100%'}).json()['events']
    ] == ['OPS-7']
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': '%'}).json()['events']] == [
        'OPS-7'
    ]
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': '_'}).json()['events']] == [
        'OPS-7'
    ]
    assert client.get('/api/events', headers=HEADERS, params={'source': 'jira'}).json()['events'] != []
    assert client.get('/api/events/facets', headers=HEADERS).json() == {
        'sources': ['jira'],
        'types': [
            'jira.ticket.created',
            'jira.ticket.updated',
            'memory.deleted',
            'memory.restored',
            'memory.saved',
            'memory.updated',
            'schedule.fired',
        ],
        'statuses': ['pending'],
        'handlers': [],
        'schedules': [],
    }
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
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other-x','Other') RETURNING id").fetchone()[
            'id'
        ]
    [foreign] = seed(store, 1, project_id=other)
    assert client.get('/api/events', headers=HEADERS).json()['events'] == []
    assert client.get(f'/api/events/{foreign}', headers=HEADERS).status_code == 404


def test_a_send_to_agent_run_shows_its_task(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [root] = seed(store, 1)
    handler = uuid4()
    key = f'agent-task:{root}:{handler}'
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            """INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,agent_task_id)
            VALUES (%s,'agent.response','succeeded','Triage · x DEMO-1','Done.',%s,%s)""",
            (project, root, key),
        )
        notification = conn.execute('SELECT max(id) AS id FROM ka.notifications').fetchone()['id']
        conn.execute(
            """INSERT INTO ka.agent_tasks(id,project_id,agent_id,agent_name,title,prompt,event_id,handler_id,status,
            attempts,finished_at,notification_id) VALUES (%s,%s,'triage','Triage','Triage · x DEMO-1','Hi',%s,%s,'succeeded',2,
            now(),%s)""",
            (key, project, root, handler, notification),
        )
        conn.execute(
            """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,agent_task_id)
            VALUES (%s,%s,'Triage','succeeded','Queued for Triage.',%s)""",
            (root, handler, key),
        )
    [run] = client.get(f'/api/events/{root}', headers=HEADERS).json()['event']['runs']
    assert (run['agent_id'], run['agent_session_id'], run['run'], run['agent_task']) == (
        'triage',
        None,
        None,
        {'id': key, 'status': 'succeeded', 'attempts': 2, 'notification_id': notification},
    )


# --- POST /api/events: events made by hand -----------------------------------------------------------------------

import pytest  # noqa: E402

NEW = {'type': 'jira.ticket.created', 'subject': 'DEMO-9', 'title': 'Try me', 'payload': {'labels': ['agent']}}


def post(client, body):
    return client.post('/api/events', headers=HEADERS, json=body)


def test_create_a_manual_event(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = post(client, NEW)
    assert response.status_code == 201, response.text
    event = response.json()['event']
    assert (
        event['source'],
        event['source_id'],
        event['status'],
        event['depth'],
        event['parent_id'],
        event['handler_id'],
    ) == ('manual', None, 'pending', 0, None, None)
    assert (event['type'], event['subject'], event['title'], event['payload']) == (
        NEW['type'],
        'DEMO-9',
        'Try me',
        {'labels': ['agent']},
    )
    assert (
        event['root_id'] == event['id']
        and event['dedupe_key'].startswith('manual:')
        and event['created_manually'] is True
    )
    assert event['chain'][0]['id'] == event['id'] and event['runs'] == []
    assert client.get(f'/api/events/{event["id"]}', headers=HEADERS).json()['event'] == event
    assert 'manual' in client.get('/api/events/facets', headers=HEADERS).json()['sources']
    with store.connect() as conn:
        audit = conn.execute(
            "SELECT action,resource_type,resource_id,actor_id,details FROM ka.audit_events WHERE action='event.created'"
        ).fetchall()
    assert len(audit) == 1 and audit[0]['resource_type'] == 'event' and audit[0]['actor_id'] is not None
    assert audit[0]['details'] == {
        'event_id': event['id'],
        'type': NEW['type'],
        'subject': 'DEMO-9',
        'source': 'manual',
    }


def test_defaults_and_trimmed_subject(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    event = post(client, {'type': 'a.b', 'subject': '  X  '}).json()['event']
    assert (event['subject'], event['title'], event['payload']) == ('X', '', {})


def test_a_hand_made_jira_event_has_no_connection(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    event = post(client, {**NEW, 'source': 'jira'}).json()['event']
    assert (event['source'], event['source_id'], event['created_manually']) == ('jira', None, True)
    assert event['dedupe_key'].startswith('manual:')
    seeded = seed(store, 1)[0]
    assert client.get(f'/api/events/{seeded}', headers=HEADERS).json()['event']['created_manually'] is False


def message(response):
    assert response.status_code == 400, response.text
    return response.json()['error']['message']


@pytest.mark.parametrize(
    'body,expected',
    [
        ({**NEW, 'type': 'a.b\n'}, 'Type'),
        ({**NEW, 'type': 'a.b '}, 'Type'),
        ({**NEW, 'type': 'Created'}, 'Type'),
        ({**NEW, 'type': 'jira'}, 'Type'),
        ({**NEW, 'type': 'a.b\u0000'}, 'Type'),
        ({**NEW, 'subject': '   '}, 'Subject'),
        ({**NEW, 'subject': 'x' * 501}, 'Subject'),
        ({**NEW, 'subject': 'a\u0000'}, 'Subject'),
        ({**NEW, 'title': 'x' * 501}, 'Title'),
        ({**NEW, 'title': 'a\u0000'}, 'Title'),
        ({**NEW, 'payload': [1]}, 'Payload'),
        ({**NEW, 'payload': 'x'}, 'Payload'),
        ({**NEW, 'payload': {'a': 'x' * 102_401}}, 'Payload'),
        ({**NEW, 'payload': {'a': [{'b': 'x\u0000'}]}}, 'Payload'),
        ({**NEW, 'payload': {'a\u0000': 1}}, 'Payload'),
        ({**NEW, 'payload': {'a': [[[{}]]]}, 'title': ' '.join(['x'] * 300)}, 'Title'),
        ({**NEW, 'source': 'handler'}, 'Source'),
        ({**NEW, 'source': 'mail'}, 'Source'),
        ({**NEW, 'status': 'processed'}, None),
        ({'subject': 'x'}, None),
    ],
)
def test_invalid_bodies_are_400(store, tmp_path, body, expected):
    client = _client(tmp_path, repository_store=store)
    text = message(post(client, body))
    if expected:
        assert text.startswith(expected), text
    assert client.get('/api/events', headers=HEADERS).json()['events'] == []


@pytest.mark.parametrize('literal', ['NaN', 'Infinity', '-Infinity'])
def test_nan_and_infinity_in_the_payload_are_400(store, tmp_path, literal):
    client = _client(tmp_path, repository_store=store)
    body = '{"type":"a.b","subject":"X","payload":{"n":[%s]}}' % literal
    response = client.post('/api/events', headers={**HEADERS, 'Content-Type': 'application/json'}, content=body)
    assert message(response) == 'Payload must be valid JSON (no NaN or Infinity).'


def test_the_payload_limit_counts_utf8_bytes(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert post(client, {**NEW, 'payload': {'text': 'ж' * 30_000}}).status_code == 201  # ~60 KB
    assert message(post(client, {**NEW, 'payload': {'text': 'ж' * 60_000}})).startswith(
        'Payload is too large'
    )  # ~120 KB


def test_deep_payloads_are_400_not_500(store, tmp_path):
    client = _client(tmp_path, repository_store=store)

    def nested(levels):
        value = {}
        for _ in range(levels - 1):
            value = {'a': value}
        return value

    assert post(client, {**NEW, 'payload': nested(32)}).status_code == 201
    assert message(post(client, {**NEW, 'payload': nested(33)})) == 'Payload is nested too deeply (at most 32 levels).'
    deep = '{"type":"a.b","subject":"X","payload":' + '{"a":' * 5000 + '1' + '}' * 5000 + '}'
    response = client.post('/api/events', headers={**HEADERS, 'Content-Type': 'application/json'}, content=deep)
    assert response.status_code == 400


def test_title_is_trimmed(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert post(client, {**NEW, 'title': '  Hi  '}).json()['event']['title'] == 'Hi'
    assert post(client, {**NEW, 'title': '   '}).json()['event']['title'] == ''


def test_the_list_counts_handler_runs_and_filters_on_them(store, tmp_path):
    """`handlers` is how many handlers ran on the event; `handled=yes|no` keeps events with or without any."""
    client = _client(tmp_path, repository_store=store)
    quiet, once, twice = seed(store, 3)
    with store.connect() as conn:
        for event, count in ((once, 1), (twice, 2)):
            for number in range(count):
                conn.execute(
                    """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message)
                    VALUES (%s,%s,%s,'succeeded','')""",
                    (event, uuid4(), f'Handler {number}'),
                )
    listed = {e['id']: e['handlers'] for e in client.get('/api/events', headers=HEADERS).json()['events']}
    assert listed == {quiet: 0, once: 1, twice: 2}
    handled = client.get('/api/events?handled=yes', headers=HEADERS).json()['events']
    assert sorted(e['id'] for e in handled) == sorted([once, twice])
    not_handled = client.get('/api/events?handled=no', headers=HEADERS).json()['events']
    assert [e['id'] for e in not_handled] == [quiet]
    assert client.get('/api/events?handled=maybe', headers=HEADERS).status_code in {400, 422}


def test_facet_types_merge_recorded_types_with_seen_ones(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    types = client.get('/api/events/facets', headers=HEADERS).json()['types']
    assert types == sorted(types) and 'schedule.fired' in types and 'memory.saved' in types
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for type in ('custom.thing', 'memory.saved'):
            conn.execute(
                "INSERT INTO ka.events(project_id,source,type,subject,dedupe_key,payload,occurred_at) VALUES (%s,'manual',%s,'s',%s,'{}',now())",
                (project, type, f'manual:{type}'),
            )
    merged = client.get('/api/events/facets', headers=HEADERS).json()['types']
    assert merged == sorted({*types, 'custom.thing'}) and merged.count('memory.saved') == 1
