"""Offer as an expert: the switch on the agent, its rules, and what clone and Apply to original do with it."""

from knowledge_admin.agents.profiles_api import EXPERT_NEEDS_WHEN, ProfileInput
from test_agent_apply import apply, preview
from test_agent_clones import clone
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_profiles import create, update
from test_repositories import HEADERS


def profile(client, agent_id):
    return client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()


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
    kept = client.put(f'/api/agents/{agent["id"]}', headers=HEADERS, json={**body, 'name': 'Payments 2'})
    assert kept.status_code == 200 and kept.json()['expert'] is True
    cleared = update(client, kept.json(), when_to_use='')
    assert cleared.status_code == 400  # an expert keeps its When to use
    listed = {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}
    assert listed[agent['id']]['expert'] is True


def test_a_clone_starts_as_no_expert(store, saved_agent):
    client = saved_agent[0]
    lead = update(client, create(client, 'Lead'), expert=True, when_to_use='Leads.').json()
    assert profile(client, clone(client, lead['id'])['id'])['expert'] is False


def test_apply_to_original_keeps_the_originals_switch(store, saved_agent):
    client = saved_agent[0]
    original = update(client, create(client, 'Payments'), expert=True, when_to_use='Payments.').json()
    copy = clone(client, original['id'])
    applied = apply(client, copy['id'], preview(client, copy['id']))
    assert applied.status_code == 200, applied.text
    assert profile(client, original['id'])['expert'] is True
