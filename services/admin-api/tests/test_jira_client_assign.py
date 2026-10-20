"""JiraClient: looking a user up by key and assigning an issue."""

import json

import httpx
import pytest
from fastapi import HTTPException
from knowledge_admin.jira.client import JiraClient


def client(handler) -> JiraClient:
    return JiraClient('https://jira.internal/jira', 'private-jira-token', httpx.MockTransport(handler))


def test_user_by_key_gets_the_user_by_key():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, query=dict(request.url.params), method=request.method)
        return httpx.Response(200, json={'key': 'ana', 'name': 'ana.p', 'displayName': 'Ana Petrov'})

    with client(handler) as jira:
        user = jira.user_by_key('ana')
    assert seen == {'path': '/jira/rest/api/2/user', 'query': {'key': 'ana'}, 'method': 'GET'}
    assert user['name'] == 'ana.p'


@pytest.mark.parametrize('name', ['ana.p', None])
def test_assign_puts_the_login_name_or_null(name):
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, method=request.method, body=json.loads(request.content))
        return httpx.Response(204)

    with client(handler) as jira:
        assert jira.assign('FIN-7', name) is None
    assert seen == {'path': '/jira/rest/api/2/issue/FIN-7/assignee', 'method': 'PUT', 'body': {'name': name}}


@pytest.mark.parametrize(
    ('status', 'expected'), [(401, 403), (403, 403), (404, 404), (400, 400), (429, 503), (302, 502), (500, 502)]
)
def test_assign_maps_errors(status, expected):
    with client(lambda request: httpx.Response(status, text='secret')) as jira:
        with pytest.raises(HTTPException) as error:
            jira.assign('FIN-7', 'ana.p')
    assert error.value.status_code == expected
    assert 'secret' not in str(error.value.detail)


def test_assign_maps_network_errors():
    def handler(request):
        raise httpx.ConnectError('down')

    with client(handler) as jira:
        with pytest.raises(HTTPException) as error:
            jira.assign('FIN-7', 'ana.p')
    assert error.value.status_code == 502
