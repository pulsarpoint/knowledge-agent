import runpy
from pathlib import Path

import httpx
import pytest

from test_api import _client
from test_jira import SETTINGS, jira_response
from test_repositories import HEADERS

MOCK = runpy.run_path(str(Path(__file__).resolve().parents[3] / 'deploy/jira-mock/server.py'))


def test_custom_jql_is_forwarded_and_only_successful_query_is_saved(store, tmp_path):
    query = 'project = DEMO AND status = "Open" ORDER BY updated DESC'
    rejected = False

    def handler(request):
        if request.url.path.endswith('/myself'):
            return jira_response(request)
        if rejected:
            return httpx.Response(400, text='private-jira-token')
        assert request.url.params['jql'] == query
        rows = MOCK['filter_issues'](query)
        return httpx.Response(200, json={'startAt': 0, 'total': len(rows), 'issues': rows[:2]})

    client = _client(tmp_path, repository_store=store, jira_transport=httpx.MockTransport(handler))
    assert client.put('/api/jira/settings', headers=HEADERS, json=SETTINGS).status_code == 200
    response = client.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 2, 'jql': query})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['connection']['jql'] == query and all(row['status_name'] == 'Open' for row in saved['tickets'])
    assert saved['tickets'][0]['jira_project_key'] == 'DEMO'
    rejected = True
    failed = client.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 2, 'jql': 'invalid query'})
    assert failed.status_code == 400 and 'private-jira-token' not in failed.text
    assert client.get('/api/jira/tickets', headers=HEADERS).json() == saved


def test_mock_jql_filters_and_rejects_unsupported_expressions():
    assert len(MOCK['filter_issues']('project = DEMO AND status = "Open" ORDER BY updated DESC')) == 25
    assert [issue['key'] for issue in MOCK['filter_issues']('key = DEMO-1')] == ['DEMO-1']
    assert MOCK['filter_issues']('text ~ "nonexistent text"') == []
    assert len(MOCK['filter_issues']('text ~ "final pagination evidence"')) == 75
    for query in [
        'project = DEMO OR status = Open',
        'status in (Open, Done)',
        'project = DEMO AND',
        'ORDER BY created ASC',
    ]:
        with pytest.raises(ValueError):
            MOCK['filter_issues'](query)


def test_mock_filter_examples():
    assert [issue['key'] for issue in MOCK['filter_issues']('key = DEMO-1')] == ['DEMO-1']
    assert MOCK['filter_issues']('text ~ "nonexistent text"') == []
    assert len(MOCK['filter_issues']('text ~ "final pagination evidence"')) == 75
    for query in [
        'project = DEMO OR status = Open',
        'status in (Open, Done)',
        'project = DEMO AND',
        'ORDER BY created ASC',
    ]:
        with pytest.raises(ValueError):
            MOCK['filter_issues'](query)
