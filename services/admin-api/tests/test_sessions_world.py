"""The Ask agent world shared by the sessions tests: a document with a linked server, and the start request."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from inventory_rows import environment_id

from live_documents import write_live_document
from test_discovery_runs import ssh_ready
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS


@pytest.fixture
def ask(sessions):
    s = sessions
    web = s.client.post(
        '/api/servers', headers=HEADERS, json={'name': 'web-1', 'hostname': '10.0.0.7', 'ssh_allowed': True}
    ).json()
    ssh_ready(SimpleNamespace(client=s.client, server=web), web)
    document = write_live_document(
        s.store,
        'payments-runbook',
        'Payments runbook',
        '# Payments runbook\n\n## Backup\n\nOld steps.',
        service_ids=[s.service],
        server_ids=[web['id']],
    )
    body = {
        'started_from': {'document_id': document['id']},
        'message': 'Add a section on log rotation.\nKeep it short.',
        'model_id': s.gpt,
        'system_prompt': 'You revise documents.',
        'context': f'Document: Payments runbook (id {document["id"]})',
        'resources': {
            'service_ids': [s.service],
            'server_ids': [web['id']],
            'documents': [{'id': document['id'], 'editable': True}],
        },
        'expert_ids': [],
    }
    return SimpleNamespace(s=s, web=web, document=document, body=body)


def post(ask, path='', **change):
    return ask.s.client.post(f'/api/sessions{path}', headers=HEADERS, json={**ask.body, **change})


@pytest.fixture
def keyless(ask):
    """A service deployed only on an enabled, SSH-allowed server that has no SSH key."""
    server = ask.s.client.post(
        '/api/servers', headers=HEADERS, json={'name': 'bare-1', 'hostname': '10.0.0.9', 'ssh_allowed': True}
    ).json()
    service = uuid4()
    with ask.s.store.connect() as conn:
        project = ask.s.store.project(conn, 'shop')
        conn.execute("UPDATE ka.servers SET ssh_allowed=true,enabled=true,username='ops' WHERE id=%s", (server['id'],))
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'Ledger')", (service, project))
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment_id,runtime_name,port)
            VALUES (%s,%s,%s,%s,'ledger',8080)""",
            (project, service, server['id'], environment_id(conn, project, 'Production')),
        )
    return SimpleNamespace(server=server, service=str(service))
