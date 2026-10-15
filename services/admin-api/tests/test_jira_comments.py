"""JiraClient writes: posting a comment on an issue."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException
from knowledge_admin.jira.client import JiraClient


def client(handler) -> JiraClient:
    return JiraClient('https://jira.internal/jira', 'private-jira-token', httpx.MockTransport(handler))


def test_add_comment_posts_the_body_and_returns_id_and_created():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, method=request.method, body=json.loads(request.content))
        seen['auth'] = request.headers['Authorization']
        return httpx.Response(201, json={'id': '10500', 'created': '2026-10-15T10:00:01.000+0000', 'body': 'x'})

    with client(handler) as jira:
        comment = jira.add_comment('10001', 'The answer')
    assert seen == {
        'path': '/jira/rest/api/2/issue/10001/comment',
        'method': 'POST',
        'body': {'body': 'The answer'},
        'auth': 'Bearer private-jira-token',
    }
    assert comment == {'id': '10500', 'created': datetime(2026, 10, 15, 10, 0, 1, tzinfo=timezone.utc)}


@pytest.mark.parametrize(
    'response,status',
    [
        (httpx.Response(400, json={}), 400),
        (httpx.Response(401), 403),
        (httpx.Response(403), 403),
        (httpx.Response(404), 404),
        (httpx.Response(429), 503),
        (httpx.Response(500), 502),
        (httpx.Response(302, headers={'Location': 'https://sso'}), 502),
        (httpx.Response(201, content=b'not json'), 502),
        (httpx.Response(201, json={'id': '1'}), 502),  # no created
        (httpx.Response(201, json={'created': '2026-10-15T10:00:01.000+0000'}), 502),  # no id
        (httpx.Response(201, json={'id': None, 'created': '2026-10-15T10:00:01.000+0000'}), 502),
        (httpx.Response(201, json={'id': True, 'created': '2026-10-15T10:00:01.000+0000'}), 502),
        (httpx.Response(201, json={'id': {}, 'created': '2026-10-15T10:00:01.000+0000'}), 502),
        (httpx.Response(201, json={'id': '', 'created': '2026-10-15T10:00:01.000+0000'}), 502),
    ],
)
def test_add_comment_failures(response, status):
    with client(lambda request: response) as jira, pytest.raises(HTTPException) as caught:
        jira.add_comment('10001', 'The answer')
    assert caught.value.status_code == status
    assert 'private-jira-token' not in str(caught.value.detail)


def test_a_timeout_is_504():
    def handler(request):
        raise httpx.ReadTimeout('slow', request=request)

    with client(handler) as jira, pytest.raises(HTTPException) as caught:
        jira.add_comment('10001', 'x')
    assert caught.value.status_code == 504
