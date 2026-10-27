"""The drafts of one conversation (GET /api/documentation/drafts?session_id=): every draft the run created or edited,
new documents and drafts of existing documents, with what became of each; and the document tools take the run's own
draft id (a person mentions a draft in the chat by its id)."""

import json
from types import SimpleNamespace
from uuid import uuid4

from psycopg.types.json import Jsonb

from access_helpers import entry
from document_tools_world import call, rev, run_drafts  # noqa: F401  (fixture)
from drafts_world import API, docs, promote  # noqa: F401  (fixture)
from knowledge_admin.capabilities.document_tools import DRAFT_NOTE
from session_rows import a_model
from test_document_drafts_tools import created_id, environment_chat, inventory
from test_repositories import HEADERS


def session_drafts(rev, session_id=None) -> list[dict]:
    response = rev.docs.client.get(f'{API}/drafts', params={'session_id': str(session_id or rev.sid)}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()['drafts']


def other_run(rev) -> dict:
    """A second run of the same person, with the same access."""
    sid = uuid4()
    with rev.docs.store.connect() as conn:
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,agent_config,turns) VALUES (%s,%s,NULL,%s,%s,'Other','{}','{}','documents',%s,1)""",
            (sid, rev.docs.project, actor, a_model(conn, actor), Jsonb({})),
        )
    return {**rev.config, 'session_id': str(sid)}


def test_a_conversations_drafts_are_its_new_documents_and_its_drafts_of_documents_with_their_state(rev):
    found = inventory(rev)
    environment_chat(rev, found)
    rev.config['access']['resources'].append({**entry('document', rev.docs.runbook, 'Runbook'), 'source': 'given'})
    service = {'type': 'service', 'id': found['service']}
    alerts = created_id(call(rev, 'new_document', resource=service, title='Alerts', content='# Alerts'))
    call(rev, 'update_document', document_id=str(rev.docs.runbook), content='# Runbook\n\nNew.')
    [_, edit] = run_drafts(rev)
    backups = created_id(call(rev, 'new_document', resource=service, title='Backups', content='# Backups'))
    # another run's draft is not this conversation's
    call(SimpleNamespace(config=other_run(rev)), 'new_document', resource=service, title='Disks', content='# D')

    listed = session_drafts(rev)
    assert [
        (item['id'], item['title'], item['draft_of'], item['draft_of_title'], item['state']) for item in listed
    ] == [
        (alerts, 'Alerts', None, None, 'open'),
        (str(edit['id']), 'Runbook', str(rev.docs.runbook), 'Runbook', 'open'),
        (backups, 'Backups', None, None, 'open'),
    ]
    assert all(item['updated_at'] for item in listed)

    # a person publishes Alerts and the Runbook edit, and discards Backups
    for draft_id in (alerts, edit['id']):
        detail = rev.docs.client.get(f'{API}/drafts/{draft_id}', headers=HEADERS).json()
        assert promote(rev.docs, detail).status_code == 200
    detail = rev.docs.client.get(f'{API}/drafts/{backups}', headers=HEADERS).json()
    discarded = rev.docs.client.post(
        f'{API}/drafts/{backups}/discard', headers=HEADERS, json={'revision': detail['revision']}
    )
    assert discarded.status_code == 200
    assert [(item['id'], item['title'], item['state']) for item in session_drafts(rev)] == [
        (alerts, 'Alerts', 'published'),
        (str(edit['id']), 'Runbook', 'published'),
        (backups, None, 'discarded'),  # a discarded new document leaves no title behind
    ]


def test_the_session_filter_takes_no_other_filter_and_an_unknown_session_has_no_drafts(rev):
    assert session_drafts(rev, uuid4()) == []
    both = rev.docs.client.get(f'{API}/drafts', params={'session_id': str(rev.sid), 'new': 'true'}, headers=HEADERS)
    assert both.status_code == 400


def test_the_document_tools_take_the_runs_draft_id_of_a_document(rev):
    """A mention gives a draft id: get_document reads that draft, update_document and edit_document save it."""
    call(rev, 'update_document', document_id=str(rev.docs.runbook), content='# Runbook\n\nOne.\n')
    [draft] = run_drafts(rev)
    draft_id = str(draft['id'])
    read = json.loads(call(rev, 'get_document', document_id=draft_id))
    assert (read['id'], read['reads'], read['markdown']) == (str(rev.docs.runbook), 'your draft', '# Runbook\n\nOne.\n')
    assert call(rev, 'update_document', document_id=draft_id, content='# Runbook\n\nTwo.\n') == (
        f'Saved draft (3 lines). {DRAFT_NOTE}'
    )
    assert call(rev, 'edit_document', document_id=draft_id, old_text='Two.', new_text='Three.') == (
        f'Saved draft (3 lines). {DRAFT_NOTE}'
    )
    [after] = run_drafts(rev)
    assert (after['id'], after['markdown'], after['revision']) == (draft['id'], '# Runbook\n\nThree.\n', 3)


def test_the_document_tools_take_the_runs_new_document_id(rev):
    found = inventory(rev)
    environment_chat(rev, found)
    new = created_id(
        call(rev, 'new_document', resource={'type': 'server', 'name': 'web-1'}, title='Disks', content='# D')
    )
    assert json.loads(call(rev, 'get_document', document_id=new))['reads'] == 'your draft'
    assert call(rev, 'edit_document', document_id=new, old_text='# D', new_text='# Disks') == (
        f'Saved draft (1 lines). {DRAFT_NOTE}'
    )


def test_another_runs_draft_id_is_not_this_runs(rev):
    call(
        SimpleNamespace(config=other_run(rev)), 'update_document', document_id=str(rev.docs.runbook), content='# Theirs'
    )
    [theirs] = [
        row for row in rev.docs.client.get(f'{API}/drafts', headers=HEADERS).json()['drafts'] if row['session_id']
    ]
    refused = call(rev, 'get_document', document_id=theirs['id'])
    assert refused.startswith('This document is not available in this run.')
    assert call(rev, 'update_document', document_id=theirs['id'], content='# Mine').startswith(
        'This document is not available in this run.'
    )
    assert run_drafts(rev) == []


def test_an_old_new_document_id_is_refused_like_any_other_id(rev):
    refused = call(rev, 'update_document', document_id=f'new:{uuid4()}', content='# X')
    assert refused.startswith('This document is not available in this run.')
    assert call(rev, 'get_document', document_id=f'new:{uuid4()}').startswith(
        'This document is not available in this run.'
    )
