"""Ask agent runs read and change their document only through get_document, save_document and edit_document, which write the run's
draft; a promoted, discarded or hand-edited draft makes the next save start a new one."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.capabilities.context import NOT_AVAILABLE
from knowledge_admin.tools.catalog import CATALOG, DOCUMENT_EDIT_TOOLS, RUN_ONLY_NEEDS, needs_met
from drafts_world import API, docs, make_document, promote, version_of  # noqa: F401  (docs: fixture rev builds on)
from knowledge_admin.capabilities.document_tools import outline
from knowledge_admin.knowledge_agents import session_worker as shared
from document_tools_world import RUNBOOK, call, rev, run_drafts  # noqa: F401  (fixture)
from test_repositories import HEADERS


def test_get_document_reads_the_outline_a_section_and_lines(rev):
    assert [(h['line'], h['level'], h['text']) for h in outline(RUNBOOK)] == [
        (1, 1, 'Runbook'), (5, 2, 'Backup'), (9, 3, 'Details'), (15, 2, 'Restore')]
    first = json.loads(call(rev, 'get_document', document_id=str(rev.docs.runbook)))
    assert first['reads'] == 'the live version 2' and {'line': 5, 'level': 2, 'text': 'Backup'} in first['outline']
    assert {'line': 15, 'level': 2, 'text': 'Restore'} in first['outline'] and first['lines'] == 17
    assert '# not a heading' not in [h['text'] for h in first['outline']]
    section = call(rev, 'get_document', document_id=str(rev.docs.runbook), section='backup')
    assert section.splitlines()[0] == '5: ## Backup' and '13: ```' in section and 'Restore' not in section
    assert call(rev, 'get_document', document_id=str(rev.docs.runbook), section='Deploy').startswith('No heading named "Deploy".')
    assert call(rev, 'get_document', document_id=str(rev.docs.runbook), start_line=15, end_line=17) == '15: ## Restore\n16: \n17: Steps.'


def test_get_document_says_how_long_the_document_is_for_lines_it_does_not_have(rev):
    for arguments in ({'start_line': 18}, {'start_line': 40, 'end_line': 50}, {'start_line': 10, 'end_line': 9}):
        assert call(rev, 'get_document', document_id=str(rev.docs.runbook), **arguments) == 'The document has 17 lines.'
    assert call(rev, 'get_document', document_id=str(rev.docs.runbook), end_line=2) == '1: # Runbook\n2: '
    assert call(rev, 'get_document', document_id=str(rev.docs.runbook), start_line=17, end_line=99) == '17: Steps.'


def test_get_document_cuts_long_reads_at_50000_characters(rev):
    from drafts_world import save_live
    save_live(rev.docs, rev.docs.runbook, '\n'.join(f'line {n} ' + 'x' * 90 for n in range(1, 1001)))
    text = call(rev, 'get_document', document_id=str(rev.docs.runbook), start_line=1)
    body, note = text.rsplit('\n\n', 1)
    assert len(body) <= 50_000 and note.startswith('Cut at 50,000 characters; read on with start_line=')


def test_save_document_creates_the_runs_draft_then_updates_it(rev):
    assert call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Runbook\n\nNew text.\n') == 'Saved draft (3 lines).'
    [draft] = run_drafts(rev)
    assert (str(draft['document_id']), draft['origin'], draft['status'], draft['title'], draft['markdown']) == (
        rev.docs.runbook, 'agent', 'open', 'Runbook', '# Runbook\n\nNew text.\n')
    assert str(draft['base_version_id']) == version_of(rev.docs, rev.docs.runbook)
    assert json.loads(call(rev, 'get_document', document_id=str(rev.docs.runbook)))['reads'] == 'your draft'
    call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Runbook\n\nNewer.', title='Runbook (revised)', description='Revised')
    [again] = run_drafts(rev)
    assert (again['id'], again['revision'], again['title'], again['description']) == (draft['id'], 2, 'Runbook (revised)', 'Revised')
    detail = rev.docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert [s['kind'] for s in detail['sources']] == ['parent']


def test_edit_document_replaces_one_exact_passage(rev):
    with pytest.raises(ValueError, match='This text is not in the document. Read it again with get_document.'):
        call(rev, 'edit_document', document_id=str(rev.docs.runbook), old_text='Nowhere', new_text='x')
    with pytest.raises(ValueError, match='This text appears 3 times. Include more of the surrounding text.'):
        call(rev, 'edit_document', document_id=str(rev.docs.runbook), old_text='## ', new_text='x')  # ## Backup, ### Details, ## Restore
    call(rev, 'edit_document', document_id=str(rev.docs.runbook), old_text='## Backup\n\nOld steps.\n\n', new_text='')
    [draft] = run_drafts(rev)
    assert 'Old steps.' not in draft['markdown'] and draft['markdown'].startswith('# Runbook\n\nIntro.\n\n### Details')
    with pytest.raises(Exception):
        CATALOG['edit_document'].arguments.model_validate({'old_text': '', 'new_text': 'x'})


def test_a_decided_or_edited_draft_makes_the_next_save_a_new_draft(rev):
    call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# One')
    [first] = run_drafts(rev)
    assert promote(rev.docs, first).status_code == 200
    assert call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Two') == (
        'Saved draft (1 lines). Your earlier draft was promoted; this is a new draft based on version 3.')
    second = run_drafts(rev)[-1]
    rev.docs.client.post(f"{API}/drafts/{second['id']}/discard", headers=HEADERS, json={'revision': second['revision']})
    assert call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Three').endswith('Your earlier draft was discarded; this is a new draft based on version 3.')
    third = run_drafts(rev)[-1]
    edited = rev.docs.client.put(f"{API}/drafts/{third['id']}", headers=HEADERS, json={
        'revision': third['revision'], 'title': 'Runbook', 'description': '', 'purpose': 'explanation', 'audience': 'both',
        'tags': [], 'markdown': '# Three, edited by a person'})
    assert edited.status_code == 200, edited.text
    assert call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Four').endswith(
        'A person edited your earlier draft; this is a new draft, and get_document now reads it.')
    assert [d['markdown'] for d in run_drafts(rev)] == ['# One', '# Two', '# Three, edited by a person', '# Four']


def test_promoting_a_revision_draft_leaves_the_run_alone(rev):
    call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Promoted')
    [draft] = run_drafts(rev)
    assert promote(rev.docs, draft).status_code == 200
    with rev.docs.store.connect() as conn:
        row = conn.execute('SELECT document_id,target_document_id FROM ka.agent_sessions WHERE id=%s', (rev.sid,)).fetchone()
    assert row['document_id'] is None and str(row['target_document_id']) == rev.docs.runbook


def test_parallel_saves_end_in_one_draft(rev):
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda text: call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown=text), ['# A', '# B']))
    [draft] = run_drafts(rev)
    assert draft['markdown'] in ('# A', '# B') and draft['revision'] == 2


def test_saves_record_the_documents_the_run_read(rev):
    """Up to the running turn (turn 1 here): a read keyed to a later turn is not the run's yet."""
    other, later = make_document(rev.docs, 'overview', 'Overview'), make_document(rev.docs, 'later', 'Later')
    with rev.docs.store.connect() as conn:
        for turn, document in ((1, other), (2, later)):
            conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
                VALUES (%s,%s,1,'tool_call','ad-hoc',%s)''', (rev.docs.project, f'cc:{rev.sid}:{turn}',
                Jsonb({'name': 'get_document', 'call_id': f'c{turn}', 'arguments': {'document_id': document}})))
    call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# With sources')
    call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# With sources, again')
    [draft] = run_drafts(rev)
    detail = rev.docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert [(s['kind'], s['source_title']) for s in detail['sources']] == [('parent', 'Runbook'), ('read', 'Overview')]
    with rev.docs.store.connect() as conn:
        audited = conn.execute("""SELECT details FROM ka.audit_events WHERE action='document.draft.updated'
            AND resource_id=%s""", (draft['id'],)).fetchone()['details']
    assert audited == {'session_id': str(rev.sid), 'turn': 1}


def test_a_closed_run_or_a_deleted_document_cannot_save(rev):
    with rev.docs.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (rev.sid,))
    with pytest.raises(ValueError, match='This run is closed.'):
        call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Late')
    current = rev.docs.client.get(f'{API}/documents/{rev.docs.runbook}', headers=HEADERS).json()
    deleted = rev.docs.client.delete(f"{API}/documents/{rev.docs.runbook}?revision={current['revision']}&confirmed=true", headers=HEADERS)
    assert deleted.status_code in (200, 204), deleted.text
    assert call(rev, 'get_document', document_id=str(rev.docs.runbook)) == 'Document not found'
    assert call(rev, 'get_document', document_id=str(uuid4())) == NOT_AVAILABLE.format(kind='document')
    with pytest.raises(ValueError, match='This document no longer exists.'):
        call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Gone')




def test_a_turn_refreshes_the_sources_of_every_draft_the_run_wrote(rev):
    overview, third = make_document(rev.docs, 'overview', 'Overview'), make_document(rev.docs, 'notes', 'Notes')
    rev.config['document_access'].append({'id': overview, 'title': 'Overview', 'editable': True})
    call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Saved by the tool')
    call(rev, 'save_document', document_id=overview, markdown='# Overview saved')
    with rev.docs.store.connect() as conn:
        conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) SELECT id,1,actor_id,%s FROM ka.agent_sessions WHERE id=%s',
                     ('Add a section.', rev.sid))
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,1,'tool_call','ad-hoc',%s)''', (rev.docs.project, f'cc:{rev.sid}:1',
            Jsonb({'name': 'get_document', 'call_id': 'c1', 'arguments': json.dumps({'document_id': third})})))
    [row] = [r for r in shared.claim(rev.docs.store, 'worker-1', [], 4) if r['id'] == rev.sid]
    shared.SessionRunner(rev.docs.store, row).finish_turn(1, 'waiting', '', usage=None)
    drafts = run_drafts(rev)
    assert [d['markdown'] for d in drafts] == ['# Saved by the tool', '# Overview saved']
    for draft in drafts:
        detail = rev.docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
        assert ('read', 'Notes') in [(s['kind'], s['source_title']) for s in detail['sources']]
    with rev.docs.store.connect() as conn:
        assert conn.execute('SELECT draft FROM ka.agent_sessions WHERE id=%s', (rev.sid,)).fetchone()['draft'] is None


def test_one_line_longer_than_a_read_is_cut_and_the_read_advances(rev):
    from drafts_world import save_live
    save_live(rev.docs, rev.docs.runbook, 'x' * 60_000 + '\nnext line')
    text = call(rev, 'get_document', document_id=str(rev.docs.runbook), start_line=1)
    body, note = text.rsplit('\n\n', 1)
    assert len(body) == 50_000 and body.startswith('1: xxx')
    assert note == 'Line 1 was cut at 50,000 characters; read on with start_line=2.'
    assert call(rev, 'get_document', document_id=str(rev.docs.runbook), start_line=2) == '2: next line'




def test_a_deadlock_with_a_document_delete_is_retried_once(rev, monkeypatch):
    from psycopg import errors
    from knowledge_admin.capabilities import document_tools
    real, attempts = document_tools._save_once, []

    def flaky(config, document_id, change):
        attempts.append(1)
        if len(attempts) == 1:
            raise errors.DeadlockDetected('deadlock')
        return real(config, document_id, change)
    monkeypatch.setattr(document_tools, '_save_once', flaky)
    assert call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Retried') == 'Saved draft (1 lines).' and len(attempts) == 2
    assert [d['markdown'] for d in run_drafts(rev)] == ['# Retried']



def test_edits_and_saves_are_refused_outside_the_runs_documents_even_without_the_enum(rev):
    other = str(uuid4())
    refused = NOT_AVAILABLE.format(kind='document')
    for tool, arguments in (('save_document', {'markdown': '# x'}), ('edit_document', {'old_text': 'Intro.', 'new_text': 'x'})):
        with pytest.raises(ValueError, match=refused):
            call(rev, tool, document_id=other, **arguments)
    read_only = {**rev.config, 'document_access': [{'id': str(rev.docs.runbook), 'title': 'Runbook', 'editable': False}]}
    with pytest.raises(ValueError, match=refused):
        asyncio.run(CATALOG['save_document'].run({'config': read_only},
                                                 CATALOG['save_document'].arguments.model_validate({'document_id': str(rev.docs.runbook), 'markdown': '# x'})))
    assert run_drafts(rev) == []


def test_get_document_reads_the_runs_draft_once_there_is_one(rev):
    whole = json.loads(call(rev, 'get_document', document_id=str(rev.docs.runbook)))
    assert whole['reads'] == 'the live version 2' and whole['markdown'].startswith('# Runbook')
    assert {'line': 5, 'level': 2, 'text': 'Backup'} in whole['outline'] and whole['lines'] == len(RUNBOOK.splitlines())
    call(rev, 'save_document', document_id=str(rev.docs.runbook), markdown='# Runbook\n\nNew text.')
    assert json.loads(call(rev, 'get_document', document_id=str(rev.docs.runbook)))['reads'] == 'your draft'


def test_a_document_neither_in_the_run_nor_in_the_inventory_cannot_be_read(rev):
    assert call(rev, 'get_document', document_id=str(uuid4())) == NOT_AVAILABLE.format(kind='document')
    assert call(rev, 'get_document', document_id='not-an-id') == NOT_AVAILABLE.format(kind='document')


def test_the_edit_tools_need_an_editable_document_and_are_never_chosen_for_saved_agents():
    assert all(CATALOG[name].needs == 'editable_document' for name in DOCUMENT_EDIT_TOOLS)
    assert 'editable_document' in RUN_ONLY_NEEDS and 'revision' not in RUN_ONLY_NEEDS
    assert needs_met('editable_document', {'document_access': [{'id': 'd', 'title': 'T', 'editable': True}]}, None)
    assert not needs_met('editable_document', {'document_access': [{'id': 'd', 'title': 'T', 'editable': False}]}, None)
    assert not needs_met('editable_document', {}, None)
    assert CATALOG['get_document'].needs is None and not {'get_doc', 'save_doc', 'edit_doc'} & set(CATALOG)


def test_without_a_session_the_edit_tools_refuse(rev):
    no_session = {key: value for key, value in rev.config.items() if key != 'session_id'}
    with pytest.raises(ValueError, match='The document tools work only in an agent session.'):
        asyncio.run(CATALOG['save_document'].run({'config': no_session}, CATALOG['save_document'].arguments.model_validate(
            {'document_id': str(rev.docs.runbook), 'markdown': '# x'})))


def test_a_subagent_gets_no_document_access_so_the_edit_tools_are_unmet():
    from test_subagent_config import MCP, OFFERED, parent
    from knowledge_admin.knowledge_agents.subagents import subagent_config
    lead = parent(document_access=[{'id': 'd', 'title': 'T', 'editable': True}])
    assert needs_met('editable_document', lead, None)
    sub = subagent_config(lead, ['read_file'], None, offered=OFFERED, mcp_tools=MCP)
    assert 'document_access' not in sub and not needs_met('editable_document', sub, None)
