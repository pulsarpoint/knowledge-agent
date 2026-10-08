"""Test-tab runs are recorded under their run id with the real user."""
import json
from uuid import uuid4

from test_agent_runs import direct, events  # noqa: F401 (fixtures)
from test_repositories import HEADERS
from test_sdk_agent import responses_api  # noqa: F401


def test_test_tab_run_is_recorded_with_actor(direct, store):
    client, agent_id, path = direct
    run_id = str(uuid4())
    completed = events(client.post(path, headers=HEADERS, json={'id': run_id, 'task': 'Inspect this agent.'}))[-1]
    assert completed['status'] == 'completed'
    with store.connect() as conn:
        rows = conn.execute('''SELECT e.kind,e.agent_id,e.payload,p.email FROM ka.agent_run_events e
            LEFT JOIN ka.principals p ON p.id=e.actor_id WHERE e.run_key=%s ORDER BY e.seq''', (f'run:{run_id}',)).fetchall()
    kinds = [row['kind'] for row in rows]
    assert kinds[0] == 'start' and kinds[-1] == 'end' and 'message' in kinds
    assert rows[0]['payload']['kind'] == 'test' and rows[0]['payload']['title'] == 'Inspect this agent.'
    assert all(row['agent_id'] == agent_id and row['email'] == 'admin@example.test' for row in rows)
    assert 'direct-test-secret' not in json.dumps([row['payload'] for row in rows])
