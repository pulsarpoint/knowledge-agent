"""A small inventory for discovery and inventory-tool tests (a fixture other test modules import)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin.capabilities import inventory
from live_documents import live_document
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def world(store, tmp_path, monkeypatch):
    data = tmp_path / 'data'
    for folder in ('code', 'generated-documents'):
        (data / folder).mkdir(parents=True)
    monkeypatch.setenv('DATA_ROOT_DIR', str(data))
    monkeypatch.setenv('CODE_ROOT', str(data / 'code'))
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(data / 'generated-documents'))
    monkeypatch.setattr(inventory, '_store', store)
    client = _client(tmp_path, repository_store=store)
    model = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'GPT',
            'provider': 'openai',
            'model': 'gpt-5',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
        },
    ).json()['id']
    server = client.post(
        '/api/servers',
        headers=HEADERS,
        json={'name': 'web-1', 'hostname': '10.0.0.7', 'description': 'Shop front end', 'ssh_allowed': True},
    ).json()
    environment, nginx, shop = uuid4(), uuid4(), uuid4()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            "INSERT INTO ka.environments(id,project_id,name,description) VALUES (%s,%s,'Production','Live')",
            (environment, project),
        )
        conn.execute(
            'INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
            (project, environment, server['id']),
        )
        conn.execute(
            """INSERT INTO ka.services(id,project_id,name,description,owner,service_role)
            VALUES (%s,%s,'nginx','Front proxy for the shop','web team','proxy')""",
            (nginx, project),
        )
        conn.execute(
            'INSERT INTO ka.environment_services(project_id,environment_id,service_id) VALUES (%s,%s,%s)',
            (project, environment, nginx),
        )
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id,runtime,runtime_name,port)
            VALUES (%s,%s,%s,'Production',%s,'systemd','nginx',80)""",
            (project, nginx, server['id'], environment),
        )
        conn.execute(
            "INSERT INTO ka.repositories(id,project_id,slug,name,remote_url) VALUES (%s,%s,'shop','Shop','https://git.example/shop.git')",
            (shop, project),
        )
        conn.execute(
            "INSERT INTO ka.inventory_links(project_id,from_service_id,to_code_project_id,kind) VALUES (%s,%s,%s,'built_from')",
            (project, nginx, shop),
        )
        for title, column, entry in (
            ('web-1 overview', 'server_id', server['id']),
            ('nginx runbook', 'service_id', nginx),
        ):
            saved = live_document(
                conn, project, title.replace(' ', '-'), title, f'# {title}\n\nBody.', description=f'About {title}'
            )
            conn.execute(
                f'INSERT INTO ka.inventory_documents(project_id,{column},document_id) VALUES (%s,%s,%s)',
                (project, entry, saved['id']),
            )
    return SimpleNamespace(
        client=client,
        store=store,
        project=project,
        server=server,
        environment=environment,
        nginx=nginx,
        shop=shop,
        model=model,
        data=data,
    )
