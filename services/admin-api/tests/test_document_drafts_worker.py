"""A Generate documents run's finished turns write a document draft: created, then updated until a person saves it;
aimed at the document with the run's name and folder, or a new one; its sources are the documents the run read."""
import json
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from knowledge_admin import document_drafts
from knowledge_admin.documents import DocumentInput, save_document
from knowledge_admin.knowledge_agents import session_worker as shared
from test_generations import sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS


def runner(sessions) -> tuple[UUID, shared.SessionRunner]:
    sid = UUID(start(sessions)['id'])
    [row] = shared.claim(sessions.store, 'worker-1', [], 4)
    return sid, shared.SessionRunner(sessions.store, row)


def drafts_of(sessions, sid) -> list[dict]:
    with sessions.store.connect() as conn:
        return conn.execute('''SELECT id,document_id,slug,folder,title,purpose,markdown,origin,edited,revision,resource_ids,
            base_version_id FROM ka.document_drafts WHERE session_id=%s ORDER BY created_at,id''', (sid,)).fetchall()


def tool_call(sessions, sid, turn: int, seq: int, name: str, arguments: dict, *, as_text: bool = False) -> None:
    """A recorded tool call. A native session records the arguments as an object (the real shape); as_text: a JSON
    string, as the SDK hands them over."""
    with sessions.store.connect() as conn:
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,%s,'tool_call','ad-hoc',%s)''', (sessions.store.project(conn, 'shop'), f'cc:{sid}:{turn}', seq,
            Jsonb({'name': name, 'call_id': f'call-{seq}', 'arguments': json.dumps(arguments) if as_text else arguments})))


def document(sessions, slug: str, folder: str | None, markdown: str) -> dict:
    with sessions.store.connect() as conn:
        return save_document(conn, sessions.store.project(conn, 'shop'), None,
                             DocumentInput(slug=slug, title=slug.replace('-', ' ').title(), markdown=markdown), folder=folder)


def test_a_documents_turn_creates_its_draft_then_updates_it(sessions):
    sid, run = runner(sessions)
    run.finish_turn(1, 'waiting', '', draft='# Payments API v1', usage=None)
    [first] = drafts_of(sessions, sid)
    assert (first['document_id'], first['slug'], first['folder'], first['title'], first['purpose'], first['origin'],
            first['markdown']) == (None, 'payments-api-reference', 'payments-api-docs', 'Payments – API reference', 'code',
                                   'agent', '# Payments API v1')
    assert first['resource_ids'] == {'code_project_ids': [sessions.repo], 'environment_ids': [sessions.environment],
                                     'service_ids': [sessions.service], 'server_ids': [], 'deployment_ids': []}
    run.finish_turn(2, 'waiting', '', draft='# Payments API v2', usage=None)
    [second] = drafts_of(sessions, sid)
    assert (second['id'], second['markdown'], second['revision']) == (first['id'], '# Payments API v2', 2)
    run.finish_turn(3, 'failed', 'Broken.', draft='# Lost', usage=None)
    assert drafts_of(sessions, sid)[0]['markdown'] == '# Payments API v2'
    session = sessions.client.get(f'/api/generations/sessions/{sid}', headers=HEADERS).json()
    assert session['document_draft'] == {'id': str(first['id']), 'document_id': None}
    promoted = sessions.client.post(f"/api/documentation/drafts/{first['id']}/promote", headers=HEADERS, json={'revision': 2})
    assert promoted.status_code == 200, promoted.text
    created = sessions.client.get(f"/api/documentation/documents/{promoted.json()['document_id']}", headers=HEADERS).json()
    assert (created['folder'], created['code_project_ids'], created['current_version']['origin']) == (
        'payments-api-docs', [sessions.repo], 'generated')
    session = sessions.client.get(f'/api/generations/sessions/{sid}', headers=HEADERS).json()
    assert (session['document_id'], session['document_draft']) == (created['id'], None)


def test_once_a_person_saves_the_draft_the_next_turn_starts_a_new_one(sessions):
    sid, run = runner(sessions)
    run.finish_turn(1, 'waiting', '', draft='# Agent text', usage=None)
    [draft] = drafts_of(sessions, sid)
    edited = sessions.client.put(f"/api/documentation/drafts/{draft['id']}", headers=HEADERS, json={
        'revision': draft['revision'], 'title': draft['title'], 'markdown': '# Agent text, corrected by a person'})
    assert edited.status_code == 200, edited.text
    run.finish_turn(2, 'waiting', '', draft='# Agent text v2', usage=None)
    kept, new = drafts_of(sessions, sid)
    assert (kept['markdown'], kept['edited']) == ('# Agent text, corrected by a person', True)
    assert (new['markdown'], new['edited'], new['origin']) == ('# Agent text v2', False, 'agent')
    run.finish_turn(3, 'waiting', '', draft='# Agent text v3', usage=None)
    assert [item['markdown'] for item in drafts_of(sessions, sid)] == ['# Agent text, corrected by a person', '# Agent text v3']


def test_the_draft_revises_the_document_with_the_runs_name_and_folder(sessions):
    live = document(sessions, 'payments-api-reference', 'payments-api-docs', '# Live text')
    sid, run = runner(sessions)
    run.finish_turn(1, 'waiting', '', draft='# Agent text', usage=None)
    [draft] = drafts_of(sessions, sid)
    assert (draft['document_id'], draft['base_version_id'], draft['slug'], draft['folder'], draft['title']) == (
        live['id'], live['version_id'], None, None, 'Payments Api Reference')
    detail = sessions.client.get(f"/api/documentation/drafts/{draft['id']}", headers=HEADERS).json()
    assert [(s['kind'], s['source_version']) for s in detail['sources']] == [('parent', 1)]
    assert (detail['live_markdown'], detail['markdown']) == ('# Live text', '# Agent text')


def test_sources_are_the_documents_the_run_read_at_the_version_it_read(sessions):
    alpha = document(sessions, 'alpha', 'team', '# Alpha v1')
    document(sessions, 'beta', None, '# Beta')
    document(sessions, 'gamma', 'team/deep', '# Gamma')
    sid, run = runner(sessions)
    tool_call(sessions, sid, 1, 1, 'get_document', {'document_id': str(alpha['id'])})
    tool_call(sessions, sid, 1, 2, 'read_document', {'collection': 'documents', 'path': 'generated-documents/beta/README.md'},
              as_text=True)
    tool_call(sessions, sid, 1, 3, 'read_document', {'collection': 'documents', 'path': 'team/deep/gamma/README.md'})
    tool_call(sessions, sid, 1, 4, 'read_document', {'collection': 'documents', 'path': 'team/alpha/notes.md'})
    tool_call(sessions, sid, 1, 5, 'get_document', {'document_id': 'not-a-uuid'})
    tool_call(sessions, sid, 1, 6, 'list_files', {'path': 'team/alpha/README.md'})
    with sessions.store.connect() as conn:  # alpha changes after the run read it
        save_document(conn, sessions.store.project(conn, 'shop'), None,
                      DocumentInput(slug='alpha', title='Alpha', markdown='# Alpha v2', revision=1), alpha['id'])
    run.finish_turn(1, 'waiting', '', draft='# Agent text', usage=None)
    [draft] = drafts_of(sessions, sid)
    url = f"/api/documentation/drafts/{draft['id']}"
    read = lambda: sorted((s['kind'], s['source_title'], s['source_version'])
                          for s in sessions.client.get(url, headers=HEADERS).json()['sources'])
    assert read() == [('read', 'Alpha', 1), ('read', 'Beta', 1), ('read', 'Gamma', 1)]
    tool_call(sessions, sid, 2, 1, 'get_document', {'document_id': str(alpha['id'])})  # read again, now at v2
    run.finish_turn(2, 'waiting', '', draft='# Agent text v2', usage=None)
    assert read() == [('read', 'Alpha', 1), ('read', 'Beta', 1), ('read', 'Gamma', 1)]  # recomputed; first read kept


def test_a_document_draft_that_cannot_be_saved_never_fails_the_turn(sessions, monkeypatch):
    def broken(*args, **kwargs):
        raise psycopg.errors.CheckViolation('broken')
    monkeypatch.setattr(document_drafts, 'save_run_draft', broken)  # Review focus 4
    sid, run = runner(sessions)
    run.finish_turn(1, 'waiting', '', draft='# Agent text', usage=None)
    with sessions.store.connect() as conn:
        row = conn.execute('SELECT status,draft,draft_turn FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()
    assert row == {'status': 'waiting', 'draft': '# Agent text', 'draft_turn': 1}
    assert drafts_of(sessions, sid) == []


def test_save_as_draft_turns_an_old_runs_draft_into_a_document_draft(sessions):
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    url = f'/api/generations/sessions/{sid}'
    empty = client.post(url + '/document', headers=HEADERS, json={})
    assert empty.status_code == 409 and empty.json()['error']['message'] == 'There is no draft yet. Wait for the agent to finish a turn.'
    with store.connect() as conn:  # a run from before drafts: a run draft, no document draft
        conn.execute("UPDATE ka.agent_sessions SET status='waiting',turns=1,draft='# Payments API',draft_turn=1 WHERE id=%s", (sid,))
    assert client.get(url, headers=HEADERS).json()['document_draft'] is None
    saved = client.post(url + '/document', headers=HEADERS, json={})
    assert saved.status_code == 201, saved.text
    draft = saved.json()['draft']
    assert draft['document_id'] is None
    assert client.get(url, headers=HEADERS).json()['document_draft'] == draft
    assert client.get('/api/documentation/documents', headers=HEADERS).json()['total'] == 0  # nothing went live
    detail = client.get(f"/api/documentation/drafts/{draft['id']}", headers=HEADERS).json()
    assert (detail['markdown'], detail['origin'], detail['author']) == ('# Payments API', 'agent', 'admin@example.test')
    assert client.post(url + '/document', headers=HEADERS, json={}).json()['draft']['id'] == draft['id']
    assert not (sessions.data / 'generated-documents/payments-api-docs').exists()
    assert client.post(url + '/document', json={}).status_code == 403
