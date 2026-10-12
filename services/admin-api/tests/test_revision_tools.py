"""Ask agent runs read and change their document only through get_doc, save_doc and edit_doc, which write the run's
draft; a promoted, discarded or hand-edited draft makes the next save start a new one."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from drafts_world import API, docs, make_document, promote, version_of  # noqa: F401  (docs: fixture rev builds on)
from knowledge_admin.capabilities.revision import outline
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.subagents import subagent_config
from knowledge_admin.tools.catalog import CATALOG, REVISION_TOOLS, catalog_listing
from revision_world import RUNBOOK, call, rev, run_drafts  # noqa: F401  (fixture)
from test_repositories import HEADERS


def test_get_doc_reads_the_outline_a_section_and_lines(rev):
    assert [(h['line'], h['level'], h['text']) for h in outline(RUNBOOK)] == [
        (1, 1, 'Runbook'), (5, 2, 'Backup'), (9, 3, 'Details'), (15, 2, 'Restore')]
    first = call(rev, 'get_doc')
    assert 'Reads: the live version 2' in first and '5: ## Backup' in first and '15: ## Restore' in first
    assert '# not a heading' not in first and 'Length: 17 lines' in first
    section = call(rev, 'get_doc', section='backup')
    assert section.splitlines()[0] == '5: ## Backup' and '13: ```' in section and 'Restore' not in section
    assert call(rev, 'get_doc', section='Deploy').startswith('No heading named "Deploy".')
    assert call(rev, 'get_doc', start_line=15, end_line=17) == '15: ## Restore\n16: \n17: Steps.'


def test_get_doc_says_how_long_the_document_is_for_lines_it_does_not_have(rev):
    for arguments in ({'start_line': 18}, {'start_line': 40, 'end_line': 50}, {'start_line': 10, 'end_line': 9}):
        assert call(rev, 'get_doc', **arguments) == 'The document has 17 lines.'
    assert call(rev, 'get_doc', end_line=2) == '1: # Runbook\n2: '
    assert call(rev, 'get_doc', start_line=17, end_line=99) == '17: Steps.'


def test_get_doc_cuts_long_reads_at_50000_characters(rev):
    from drafts_world import save_live
    save_live(rev.docs, rev.docs.runbook, '\n'.join(f'line {n} ' + 'x' * 90 for n in range(1, 1001)))
    text = call(rev, 'get_doc', start_line=1)
    body, note = text.rsplit('\n\n', 1)
    assert len(body) <= 50_000 and note.startswith('Cut at 50,000 characters; read on with start_line=')


def test_save_doc_creates_the_runs_draft_then_updates_it(rev):
    assert call(rev, 'save_doc', markdown='# Runbook\n\nNew text.\n') == 'Saved draft (3 lines).'
    [draft] = run_drafts(rev)
    assert (str(draft['document_id']), draft['origin'], draft['status'], draft['title'], draft['markdown']) == (
        rev.docs.runbook, 'agent', 'open', 'Runbook', '# Runbook\n\nNew text.\n')
    assert str(draft['base_version_id']) == version_of(rev.docs, rev.docs.runbook)
    assert 'Reads: your draft' in call(rev, 'get_doc')
    call(rev, 'save_doc', markdown='# Runbook\n\nNewer.', title='Runbook (revised)', description='Revised')
    [again] = run_drafts(rev)
    assert (again['id'], again['revision'], again['title'], again['description']) == (draft['id'], 2, 'Runbook (revised)', 'Revised')
    detail = rev.docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert [s['kind'] for s in detail['sources']] == ['parent']


def test_edit_doc_replaces_one_exact_passage(rev):
    with pytest.raises(ValueError, match='This text is not in the document. Read it again with get_doc.'):
        call(rev, 'edit_doc', old_text='Nowhere', new_text='x')
    with pytest.raises(ValueError, match='This text appears 3 times. Include more of the surrounding text.'):
        call(rev, 'edit_doc', old_text='## ', new_text='x')  # ## Backup, ### Details, ## Restore
    call(rev, 'edit_doc', old_text='## Backup\n\nOld steps.\n\n', new_text='')
    [draft] = run_drafts(rev)
    assert 'Old steps.' not in draft['markdown'] and draft['markdown'].startswith('# Runbook\n\nIntro.\n\n### Details')
    with pytest.raises(Exception):
        CATALOG['edit_doc'].arguments.model_validate({'old_text': '', 'new_text': 'x'})


def test_a_decided_or_edited_draft_makes_the_next_save_a_new_draft(rev):
    call(rev, 'save_doc', markdown='# One')
    [first] = run_drafts(rev)
    assert promote(rev.docs, first).status_code == 200
    assert call(rev, 'save_doc', markdown='# Two') == (
        'Saved draft (1 lines). Your earlier draft was promoted; this is a new draft based on version 3.')
    second = run_drafts(rev)[-1]
    rev.docs.client.post(f"{API}/drafts/{second['id']}/discard", headers=HEADERS, json={'revision': second['revision']})
    assert call(rev, 'save_doc', markdown='# Three').endswith('Your earlier draft was discarded; this is a new draft based on version 3.')
    third = run_drafts(rev)[-1]
    edited = rev.docs.client.put(f"{API}/drafts/{third['id']}", headers=HEADERS, json={
        'revision': third['revision'], 'title': 'Runbook', 'description': '', 'purpose': 'explanation', 'audience': 'both',
        'tags': [], 'markdown': '# Three, edited by a person'})
    assert edited.status_code == 200, edited.text
    assert call(rev, 'save_doc', markdown='# Four').endswith(
        'A person edited your earlier draft; this is a new draft, and get_doc now reads it.')
    assert [d['markdown'] for d in run_drafts(rev)] == ['# One', '# Two', '# Three, edited by a person', '# Four']


def test_promoting_a_revision_draft_leaves_the_run_alone(rev):
    call(rev, 'save_doc', markdown='# Promoted')
    [draft] = run_drafts(rev)
    assert promote(rev.docs, draft).status_code == 200
    with rev.docs.store.connect() as conn:
        row = conn.execute('SELECT document_id,target_document_id FROM ka.agent_sessions WHERE id=%s', (rev.sid,)).fetchone()
    assert row['document_id'] is None and str(row['target_document_id']) == rev.docs.runbook


def test_parallel_saves_end_in_one_draft(rev):
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda text: call(rev, 'save_doc', markdown=text), ['# A', '# B']))
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
    call(rev, 'save_doc', markdown='# With sources')
    call(rev, 'save_doc', markdown='# With sources, again')
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
        call(rev, 'save_doc', markdown='# Late')
    current = rev.docs.client.get(f'{API}/documents/{rev.docs.runbook}', headers=HEADERS).json()
    deleted = rev.docs.client.delete(f"{API}/documents/{rev.docs.runbook}?revision={current['revision']}&confirmed=true", headers=HEADERS)
    assert deleted.status_code in (200, 204), deleted.text
    for tool, arguments in (('get_doc', {}), ('save_doc', {'markdown': '# Gone'})):
        with pytest.raises(ValueError, match='This document no longer exists.'):
            call(rev, tool, **arguments)


def test_the_document_tools_are_run_only_and_kept_from_subagents():
    names = {item['name'] for item in catalog_listing()}
    assert not names & set(REVISION_TOOLS) and 'propose_service' not in names
    assert all(CATALOG[name].needs == 'revision' for name in REVISION_TOOLS)


def test_a_revision_turn_never_turns_its_answer_into_a_draft_and_refreshes_sources(rev):
    call(rev, 'save_doc', markdown='# Saved by the tool')
    other = make_document(rev.docs, 'overview', 'Overview')
    with rev.docs.store.connect() as conn:
        conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) SELECT id,1,actor_id,%s FROM ka.agent_sessions WHERE id=%s',
                     ('Add a section.', rev.sid))
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,1,'tool_call','ad-hoc',%s)''', (rev.docs.project, f'cc:{rev.sid}:1',
            Jsonb({'name': 'get_document', 'call_id': 'c1', 'arguments': json.dumps({'document_id': other})})))
    [row] = [r for r in shared.claim(rev.docs.store, 'worker-1', [], 4) if r['id'] == rev.sid]
    shared.SessionRunner(rev.docs.store, row).finish_turn(1, 'waiting', '', draft='# The final answer', usage=None)
    [draft] = run_drafts(rev)
    assert draft['markdown'] == '# Saved by the tool'
    detail = rev.docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert ('read', 'Overview') in [(s['kind'], s['source_title']) for s in detail['sources']]
    with rev.docs.store.connect() as conn:
        assert conn.execute('SELECT draft FROM ka.agent_sessions WHERE id=%s', (rev.sid,)).fetchone()['draft'] is None


def test_one_line_longer_than_a_read_is_cut_and_the_read_advances(rev):
    from drafts_world import save_live
    save_live(rev.docs, rev.docs.runbook, 'x' * 60_000 + '\nnext line')
    text = call(rev, 'get_doc', start_line=1)
    body, note = text.rsplit('\n\n', 1)
    assert len(body) == 50_000 and body.startswith('1: xxx')
    assert note == 'Line 1 was cut at 50,000 characters; read on with start_line=2.'
    assert call(rev, 'get_doc', start_line=2) == '2: next line'


def test_a_subagent_of_a_revision_run_is_not_offered_the_document_tools():
    from knowledge_admin.tools.catalog import needs_met
    from test_subagent_config import parent
    sub = subagent_config({**parent(), 'revision_run': {'session_id': 's', 'document_id': 'd'}}, ['list_files'], None,
                          offered=frozenset({'list_files'}), mcp_tools={})
    assert 'revision_run' not in sub and not any(needs_met(CATALOG[name].needs, sub, None) for name in REVISION_TOOLS)


def test_a_deadlock_with_a_document_delete_is_retried_once(rev, monkeypatch):
    from psycopg import errors
    from knowledge_admin.capabilities import revision
    real, attempts = revision._save_once, []

    def flaky(config, change):
        attempts.append(1)
        if len(attempts) == 1:
            raise errors.DeadlockDetected('deadlock')
        return real(config, change)
    monkeypatch.setattr(revision, '_save_once', flaky)
    assert call(rev, 'save_doc', markdown='# Retried') == 'Saved draft (1 lines).' and len(attempts) == 2
    assert [d['markdown'] for d in run_drafts(rev)] == ['# Retried']


def test_only_an_ask_agent_run_meets_the_revision_need(rev, monkeypatch):
    """A saved agent's config and the ad-hoc base config carry the agent version as 'revision' (an int); only the
    run's 'revision_run' switch meets the need."""
    from knowledge_admin.knowledge_agents.config import get_agent_config
    from knowledge_admin.knowledge_agents.ephemeral import base_config
    from knowledge_admin.tools.catalog import needs_met
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('CODE_ROOT', '/work/data/code')
    saved = get_agent_config('search', conninfo=rev.docs.store.conninfo)
    assert isinstance(saved['revision'], int) and not needs_met('revision', saved, None)
    with rev.docs.store.connect() as conn:
        ad_hoc = base_config(conn, rev.docs.project, model={'id': 'm'}, system_prompt='x')
    assert 'revision_run' not in ad_hoc and not needs_met('revision', ad_hoc, None)
    assert not needs_met('revision', {'revision_run': 'yes'}, None)
    assert needs_met('revision', rev.config, None)
