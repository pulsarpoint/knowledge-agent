"""Generate documents: per-run agents get exactly the form's sources as their run resources; fixed agents keep their
own configuration and get the sources in the first message (fixed Claude Code agents also as working folders)."""
import json

from knowledge_admin.claude_code_api import NO_RUN_FOLDER
from test_claude_code_agents import claude_code_agent
from test_claude_code_sessions import refused, sessions, start  # noqa: F401  (fixture)
from test_run_resources import per_run  # noqa: F401  (fixture)

NONE_CHOSEN = {'code_project_ids': [], 'collection_ids': [], 'environment_ids': [], 'service_ids': [], 'server_ids': []}


def stored(store, session_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT agent_config,run_resources FROM ka.agent_sessions_cc WHERE id=%s',
                            (session_id,)).fetchone()


def test_a_per_run_native_agent_gets_exactly_the_forms_sources(per_run):
    s = per_run
    session = start(s, **{**NONE_CHOSEN, 'agent_id': s.analyst, 'code_project_ids': [s.repo],
                          'collection_ids': [s.runbooks], 'server_ids': [s.host['id']]})
    row = stored(s.store, session['id'])
    assert row['run_resources'] == {'code_project_ids': [s.repo], 'collection_ids': [s.runbooks],
                                    'server_ids': [s.host['id']], 'service_ids': []}
    pinned = row['agent_config']
    assert pinned['run_resources'] == row['run_resources']
    assert [p['id'] for p in pinned['code']['projects']] == [s.repo] and pinned['code']['all_projects'] is False
    assert [c['name'] for c in pinned['documents']['collections']] == ['runbooks']
    assert [h['id'] for h in pinned['ssh']['servers']] == [s.host['id']] and pinned['services']['items'] == []
    assert s.key['private_key'] not in json.dumps(pinned)  # references only
    assert [f['path'] for f in session['context_snapshot']['folders']] == [f'{s.data}/code/app']
    assert session['run_resources'] == row['run_resources']
    text = session['inputs'][0]['text']
    assert f'## Code\n- Payments API — {s.data}/code/app' in text
    assert '## Documents\n- runbooks: The runbooks documents' in text
    only_code = stored(s.store, start(s, **{**NONE_CHOSEN, 'agent_id': s.analyst, 'code_project_ids': [s.repo]})['id'])
    assert only_code['agent_config']['ssh']['servers'] == []  # no server chosen: none, never all


def test_generate_documents_refuses_sources_a_per_run_agent_may_not_get(per_run):
    s = per_run

    def refusal(**change) -> str:
        return refused(s, **{**NONE_CHOSEN, 'agent_id': s.analyst, **change})
    assert refusal(code_project_ids=[s.worker]) == 'Code project Payments worker is not allowed for this agent.'
    assert refusal(collection_ids=[s.private]) == 'Collection private is not allowed for this agent.'
    assert refusal(server_ids=[s.server['id']]) == 'Server prod-1 is not available: it was removed, archived or disabled.'
    # Production's Payments service brings Payments worker, which the agent may not be given.
    assert refusal(environment_ids=[s.environment]) == 'Code project Payments worker is not allowed for this agent.'
    with s.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc WHERE agent_id=%s',
                            (s.analyst,)).fetchone()['n'] == 0


def test_a_per_run_claude_code_agent_works_in_exactly_the_run_code_projects(per_run):
    s = per_run
    reader = claude_code_agent(s.client, 'Code reader', model_id=s.claude, resources='per_run',
                               repositories_enabled=True, all_repositories=True,
                               documents_enabled=True, collection_ids=[s.runbooks])
    session = start(s, **{**NONE_CHOSEN, 'agent_id': reader, 'code_project_ids': [s.worker]})
    assert [f['path'] for f in session['context_snapshot']['folders']] == [f'{s.data}/code/worker']  # not every project
    row = stored(s.store, session['id'])
    assert (row['agent_config'], row['run_resources']['code_project_ids']) == (None, [s.worker])
    assert refused(s, **{**NONE_CHOSEN, 'agent_id': reader, 'server_ids': [s.host['id']]}) == 'This agent does not use servers.'
    assert refused(s, **{**NONE_CHOSEN, 'agent_id': reader, 'collection_ids': [s.runbooks]}) == NO_RUN_FOLDER


def test_fixed_agents_keep_their_configuration_and_get_the_sources_in_the_first_message(per_run):
    s = per_run
    session = start(s, collection_ids=[s.private])  # the fixed Claude Code writer, every kind chosen
    assert stored(s.store, session['id'])['run_resources'] is None and session['run_resources'] is None
    assert '## Documents\n- private: The private documents' in session['inputs'][0]['text']
    assert [f['path'] for f in session['context_snapshot']['folders']] == [f'{s.data}/code/app', f'{s.data}/code/worker']
