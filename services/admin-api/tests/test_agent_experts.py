"""Offer as an expert: the switch on the agent, its rules, and what delete, clone and Apply to original do with it."""
from knowledge_admin.agents.profiles_api import EXPERT_NEEDS_WHEN, ProfileInput
from test_agent_apply import apply, preview
from test_agent_clones import clone
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_profiles import create, update
from test_repositories import HEADERS


def profile(client, agent_id):
    return client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()


def hide(conn, agent_id, expert_id):
    conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',false)",
                 (agent_id, f'expert:{expert_id}'))


def test_the_switch_round_trips_needs_when_to_use_and_a_client_that_omits_it_keeps_it(store, saved_agent):
    client = saved_agent[0]
    agent = create(client, 'Payments')
    assert agent['expert'] is False
    refused = update(client, agent, expert=True, when_to_use='  ')
    assert refused.status_code == 400 and refused.json()['error']['message'] == EXPERT_NEEDS_WHEN
    on = update(client, agent, expert=True, when_to_use='Questions about payments.')
    assert on.status_code == 200, on.text
    assert on.json()['expert'] is True and on.json()['revision'] == agent['revision'] + 1
    body = {field: on.json()[field] for field in ProfileInput.model_fields if field != 'expert'}
    kept = client.put(f"/api/agents/{agent['id']}", headers=HEADERS, json={**body, 'name': 'Payments 2'})
    assert kept.status_code == 200 and kept.json()['expert'] is True
    cleared = update(client, kept.json(), when_to_use='')
    assert cleared.status_code == 400  # an expert keeps its When to use
    listed = {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}
    assert listed[agent['id']]['expert'] is True


def test_deleting_an_expert_deletes_every_callers_row_for_it(store, saved_agent):
    client = saved_agent[0]
    caller, expert = create(client, 'Caller'), create(client, 'Payments')
    expert = update(client, expert, expert=True, when_to_use='Payments.').json()
    with store.connect() as conn:
        hide(conn, caller['id'], expert['id'])
    deleted = client.request('DELETE', f"/api/agents/{expert['id']}", headers=HEADERS,
                             json={'revision': expert['revision'], 'confirmed': True})
    assert deleted.status_code == 204, deleted.text
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_tools WHERE tool=%s",
                            (f"expert:{expert['id']}",)).fetchone()['n'] == 0


def test_a_clone_copies_the_callers_choices_and_starts_as_no_expert(store, saved_agent):
    client = saved_agent[0]
    other = update(client, create(client, 'Payments'), expert=True, when_to_use='Payments.').json()
    lead = update(client, create(client, 'Lead'), expert=True, when_to_use='Leads.').json()
    with store.connect() as conn:
        hide(conn, lead['id'], other['id'])
    copy = profile(client, clone(client, lead['id'])['id'])
    assert copy['expert'] is False
    with store.connect() as conn:
        rows = conn.execute('SELECT tool,mode FROM ka.agent_tools WHERE agent_id=%s AND tool LIKE %s',
                            (copy['id'], 'expert:%')).fetchall()
    assert rows == [{'tool': f"expert:{other['id']}", 'mode': 'off'}]


def test_apply_to_original_keeps_the_originals_switch_and_never_lists_it_as_its_own_expert(store, saved_agent):
    client = saved_agent[0]
    original = update(client, create(client, 'Payments'), expert=True, when_to_use='Payments.').json()
    copy = clone(client, original['id'])
    with store.connect() as conn:
        hide(conn, copy['id'], original['id'])  # the clone switches off the original as an expert
    applied = apply(client, copy['id'], preview(client, copy['id']))
    assert applied.status_code == 200, applied.text
    assert profile(client, original['id'])['expert'] is True
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tools WHERE agent_id=%s AND tool=%s',
                            (original['id'], f"expert:{original['id']}")).fetchone()['n'] == 0
