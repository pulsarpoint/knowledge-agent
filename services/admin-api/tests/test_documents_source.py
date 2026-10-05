"""Docs → Documents gives itself its RAG path at admin-api startup, also on a fresh install."""
import json
import logging
import time

import httpx
from fastapi.testclient import TestClient

from docs_wiki.config import App, Config, ServerConfig
from knowledge_admin.app import create_app, ensure_documents_source
from knowledge_admin.rag_collections import DOCUMENTS_EXCLUDE


class Rag:
    """Collections and owned paths as the RAG service keeps them (creation settings apply once)."""

    def __init__(self):
        self.collections: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.down = False

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path.split('/', 4)[-1], body))
        if self.down:
            raise httpx.ConnectError('refused')
        name = request.url.path.split('/by-name/')[-1].split('/')[0]
        if request.method == 'GET':
            return httpx.Response(200, json={'collections': list(self.collections.values())})
        row = self.collections.setdefault(name, {'name': name, 'exclude': body.get('exclude', []), 'paths': []})
        if request.url.path.endswith('/paths'):
            for other in self.collections.values():
                other['paths'] = [p for p in other['paths'] if p['owner_kind'] != body['owner_kind']]
            row['paths'].append({'source_path': body['source_path'], 'owner_kind': body['owner_kind'],
                                 'owner_id': None, 'owner_key': None})
        return httpx.Response(200, json=row)


def app_config(tmp_path):
    return (Config(apps=[App(name='shop', app_root=None)], llm=None),
            ServerConfig(cors_origin=None, session_retention_days=90, state_dir=str(tmp_path)))


def test_fresh_install_gets_the_documents_collection_once(store, monkeypatch):
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-key')
    with store.connect() as conn:  # migrations applied, no RAG workspace yet
        assert conn.execute('SELECT count(*) AS n FROM ka.rag_workspaces').fetchone()['n'] == 0
    rag = Rag()
    with httpx.Client(base_url='http://rag.test', transport=httpx.MockTransport(rag)) as client:
        ensure_documents_source(store, client, 'shop')
        assert rag.calls[1:] == [('PUT', 'collections/by-name/documents', {'exclude': DOCUMENTS_EXCLUDE}),
                                 ('PUT', 'collections/by-name/documents/paths',
                                  {'source_path': 'generated-documents', 'owner_kind': 'documents'})]
        assert rag.collections['documents']['paths'][0]['owner_kind'] == 'documents'
        rag.calls.clear()
        ensure_documents_source(store, client, 'shop')  # idempotent
        assert [method for method, *_ in rag.calls] == ['GET']


def test_documents_owned_elsewhere_are_left_alone(store, monkeypatch):
    """Migration 0063 names the collection generated-documents when `documents` was taken."""
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-key')
    rag = Rag()
    rag.collections['generated-documents'] = {'name': 'generated-documents', 'paths': [
        {'source_path': 'generated-documents', 'owner_kind': 'documents', 'owner_id': None, 'owner_key': None}]}
    with httpx.Client(base_url='http://rag.test', transport=httpx.MockTransport(rag)) as client:
        ensure_documents_source(store, client, 'shop')
    assert [method for method, *_ in rag.calls] == ['GET'] and 'documents' not in rag.collections


def test_startup_ensures_documents_and_a_rag_outage_never_blocks_it(store, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-key')
    cfg, server = app_config(tmp_path)
    rag = Rag()
    with httpx.Client(base_url='http://rag.test', transport=httpx.MockTransport(rag)) as backend:
        with TestClient(create_app(cfg, server=server, repository_store=store, backoffice_client=backend)) as client:
            assert client.get('/health').status_code == 200
            for _ in range(100):
                if rag.collections.get('documents', {}).get('paths'):
                    break
                time.sleep(.05)
        assert rag.collections['documents']['paths'][0]['source_path'] == 'generated-documents'
        rag.down = True
        with caplog.at_level(logging.ERROR, logger='knowledge_admin.app'):
            with TestClient(create_app(cfg, server=server, repository_store=store, backoffice_client=backend)) as client:
                assert client.get('/health').status_code == 200
                for _ in range(100):
                    if 'Could not add the Documents source' in caplog.text:
                        break
                    time.sleep(.05)
        assert 'Could not add the Documents source to RAG' in caplog.text
