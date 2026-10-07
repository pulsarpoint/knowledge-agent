"""Docs → Generate documents is the only way to generate: the one-shot generation and its worker are gone."""
import importlib.util
import inspect
import tomllib
from pathlib import Path
from uuid import uuid4

from knowledge_admin.documents import save_document
from test_api import _client
from test_documents import VALUE
from test_repositories import HEADERS

ROOT = Path(__file__).resolve().parents[3]


def test_the_one_shot_generation_is_gone(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for method, path in (('GET', '/api/documentation/generations'), ('POST', '/api/documentation/generations'),
                         ('GET', f'/api/documentation/generations/{uuid4()}/api-analysis'),
                         ('GET', '/api/documentation/workflows'), ('POST', '/api/documentation/workflows'),
                         ('GET', f'/api/documentation/workflows/{uuid4()}')):
        assert client.request(method, path, headers=HEADERS, json={}).status_code == 404, (method, path)
    for module in ('documentation_generation', 'documentation_workflows', 'documentation_updates', 'api_evidence'):
        assert importlib.util.find_spec(f'knowledge_admin.{module}') is None, module
    assert 'generation_id' not in inspect.signature(save_document).parameters
    scripts = tomllib.loads((ROOT / 'services/admin-api/pyproject.toml').read_text())['project']['scripts']
    assert 'knowledge-doc-worker' not in scripts
    # Saved documents stay: the document page no longer links generation workflows.
    created = client.post('/api/documentation/documents', headers=HEADERS, json=VALUE)
    assert created.status_code == 201, created.text
    detail = client.get(f"/api/documentation/documents/{created.json()['id']}", headers=HEADERS)
    assert detail.status_code == 200 and detail.json()['title'] == 'Payments runbook'
    assert 'workflows' not in detail.json()
