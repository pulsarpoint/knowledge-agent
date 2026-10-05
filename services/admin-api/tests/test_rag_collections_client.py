"""The admin-side RAG client for collections and source paths."""
import json
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

from knowledge_admin.rag_collections import Owner, RagCollections

PROJECT = UUID('00000000-0000-4000-8000-000000000001')
CODE = UUID('00000000-0000-4000-8000-0000000000c0')


def backend(handler):
    return httpx.Client(base_url='http://rag.test', transport=httpx.MockTransport(handler))


def test_paths_are_added_and_removed_by_owner():
    calls = []

    def rag(request):
        calls.append((request.method, request.url.path, dict(request.url.params),
                      json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={'removed': 1} if request.method == 'DELETE' else {'name': 'payments', 'paths': []})
    with backend(rag) as client:
        rag_client = RagCollections(client, 'k', PROJECT, None)
        rag_client.ensure_collection('payments')
        rag_client.add_path('payments', 'code/api', Owner('code_project', CODE, 'api'))
        assert rag_client.remove_path('payments', Owner('jira')) == 1
        assert rag_client.remove_paths(Owner('code_project', CODE)) == 1
    prefix = f'/backoffice/projects/{PROJECT}/'
    assert calls == [
        ('PUT', prefix + 'collections/by-name/payments', {}, {}),
        ('PUT', prefix + 'collections/by-name/payments/paths', {},
         {'source_path': 'code/api', 'owner_kind': 'code_project', 'owner_id': str(CODE), 'owner_key': 'api'}),
        ('DELETE', prefix + 'collections/by-name/payments/paths', {'owner_kind': 'jira'}, None),
        ('DELETE', prefix + 'paths', {'owner_kind': 'code_project', 'owner_id': str(CODE)}, None)]


def test_owned_paths_lists_one_owner_without_status_walks():
    def rag(request):
        assert request.url.params['status'] == 'false'
        return httpx.Response(200, json={'collections': [
            {'name': 'payments', 'paths': [
                {'source_path': 'code/api', 'owner_kind': 'code_project', 'owner_id': str(CODE), 'owner_key': 'api'},
                {'source_path': 'rag-data/jira', 'owner_kind': 'jira', 'owner_id': None, 'owner_key': None}]},
            {'name': 'other', 'paths': [
                {'source_path': 'code/x', 'owner_kind': 'code_project', 'owner_id': str(PROJECT), 'owner_key': 'x'}]}]})
    with backend(rag) as client:
        owned = RagCollections(client, 'k', PROJECT, None).owned_paths('code_project', CODE)
    assert owned == [{'collection': 'payments', 'source_path': 'code/api', 'owner_kind': 'code_project',
                      'owner_id': str(CODE), 'owner_key': 'api'}]


def test_rag_errors_keep_their_message_and_outages_are_502():
    conflict = lambda request: httpx.Response(409, json={'error': {'message': 'Path overlaps code in collection payments'}})
    with backend(conflict) as client:
        with pytest.raises(HTTPException) as error:
            RagCollections(client, 'k', PROJECT, None).add_path('payments', 'code/api')
    assert (error.value.status_code, error.value.detail) == (409, 'Path overlaps code in collection payments')

    def down(request):
        raise httpx.ConnectError('refused')
    with backend(down) as client:
        with pytest.raises(HTTPException) as error:
            RagCollections(client, 'k', PROJECT, None).remove_paths(Owner('jira'))
    assert (error.value.status_code, error.value.detail) == (502, 'RAG service is unavailable. Retry later.')
