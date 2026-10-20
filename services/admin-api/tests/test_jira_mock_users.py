import json
import runpy
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[3] / 'deploy/jira-mock/server.py'
AUTH = {'Authorization': 'Bearer jira-mock-token'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@pytest.fixture
def mock(tmp_path, monkeypatch):
    monkeypatch.setenv('JIRA_MOCK_STATE', str(tmp_path / 'state.json'))
    module = runpy.run_path(str(SERVER))
    server = ThreadingHTTPServer(('127.0.0.1', 0), module['Handler'])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    opener = urllib.request.build_opener(NoRedirect)

    def call(path, form=None, headers=AUTH):
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        try:
            with opener.open(urllib.request.Request(base + path, data=data, headers=headers)) as response:
                return response.status, json.loads(response.read() or b'null')
        except urllib.error.HTTPError as error:
            return error.code, None

    try:
        yield call, module
    finally:
        server.shutdown()
        server.server_close()
        module['reset']()


def search(call, query):
    status, users = call('/jira/rest/api/2/user/search?username=' + urllib.parse.quote(query))
    assert status == 200
    return users


def test_user_search_matches_key_name_and_display_name_case_insensitively(mock):
    call, _ = mock
    assert {user['key'] for user in search(call, 'BOT')} == {'ops-bot', 'docs-bot'}
    assert [user['key'] for user in search(call, 'petrov')] == ['ana']
    assert [user['key'] for user in search(call, 'Marko Il')] == ['marko']
    assert len(search(call, '')) == 5
    assert search(call, 'nobody') == []
    ana = search(call, 'ana')[0]
    assert ana == {
        'key': 'ana',
        'name': 'ana',
        'displayName': 'Ana Petrović',
        'emailAddress': 'ana@example.com',
        'active': True,
    }


def test_user_search_needs_the_token(mock):
    call, _ = mock
    assert call('/jira/rest/api/2/user/search?username=a', headers={})[0] == 401


def test_seeded_assignees_keep_mock_admin_and_include_bots_and_people(mock):
    call, _ = mock
    assert call('/jira/rest/api/2/issue/DEMO-1')[1]['fields']['assignee']['key'] == 'mock-admin'
    assert call('/jira/rest/api/2/issue/DEMO-4')[1]['fields']['assignee']['key'] == 'ops-bot'
    assert call('/jira/rest/api/2/issue/DEMO-8')[1]['fields']['assignee']['key'] == 'docs-bot'
    assert call('/jira/rest/api/2/issue/DEMO-12')[1]['fields']['assignee']['key'] == 'ana'
    assert call('/jira/rest/api/2/issue/DEMO-16')[1]['fields']['assignee']['key'] == 'marko'
    assert call('/jira/rest/api/2/issue/DEMO-20')[1]['fields']['assignee'] is None


def test_control_reassigns_and_unassigns_and_bumps_updated(mock):
    call, _ = mock
    before = call('/jira/rest/api/2/issue/DEMO-1')[1]['fields']
    form = {'summary': before['summary'], 'description': before['description'], 'status': 'Open'}
    assert call('/mock/DEMO-1/edit', {**form, 'assignee': 'docs-bot'})[0] == 303
    after = call('/jira/rest/api/2/issue/DEMO-1')[1]['fields']
    assert after['assignee']['key'] == 'docs-bot' and after['assignee']['displayName'] == 'Docs Bot'
    assert after['updated'] > before['updated']
    assert call('/mock/DEMO-1/edit', {**form, 'assignee': ''})[0] == 303
    assert call('/jira/rest/api/2/issue/DEMO-1')[1]['fields']['assignee'] is None
    assert call('/mock/DEMO-1/edit', {**form, 'assignee': 'ghost'})[0] == 400


def test_jql_assignee_key_and_empty(mock):
    call, _ = mock
    _, found = call('/jira/rest/api/2/search?jql=' + urllib.parse.quote('assignee = ana'))
    assert [issue['key'] for issue in found['issues']] == ['DEMO-12']
    _, found = call('/jira/rest/api/2/search?jql=' + urllib.parse.quote('assignee is EMPTY AND key = DEMO-20'))
    assert [issue['key'] for issue in found['issues']] == ['DEMO-20']


def test_comment_as_a_chosen_user_and_default_jira_user(mock):
    call, _ = mock
    assert call('/mock/DEMO-20/comment', {'body': 'from Ana', 'author': 'ana'})[0] == 303
    assert call('/mock/DEMO-20/comment', {'body': 'from default'})[0] == 303
    assert call('/mock/DEMO-20/comment', {'body': 'bad', 'author': 'ghost'})[0] == 400
    issue = call('/jira/rest/api/2/issue/DEMO-20')[1]
    comments = call('/jira/rest/api/2/issue/DEMO-20/comment?maxResults=2&startAt=5')[1]['comments']
    assert comments[0]['author']['key'] == 'ana' and comments[0]['author']['displayName'] == 'Ana Petrović'
    assert comments[1]['author']['displayName'] == 'Jira User'
    assert issue['fields']['updated'] == comments[1]['created']


def test_a_new_ticket_can_be_created_assigned_to_a_user(mock):
    call, _ = mock
    assert call('/mock/tickets', {'summary': 'Check the payments API', 'assignee': 'ops-bot'})[0] == 303
    found = call('/jira/rest/api/2/search?jql=' + urllib.parse.quote('assignee = ops-bot'))[1]
    newest = max(found['issues'], key=lambda issue: int(issue['key'].split('-')[1]))
    assert newest['fields']['summary'] == 'Check the payments API'
    assert newest['fields']['assignee']['key'] == 'ops-bot'
    assert call('/mock/tickets', {'summary': 'Nobody yet'})[0] == 303  # without an assignee: unassigned, as before
    assert call('/mock/tickets', {'summary': 'Ghost', 'assignee': 'ghost'})[0] == 400


def test_the_control_page_offers_assignees_on_new_tickets_and_lists_them(mock):
    _, module = mock
    page = module['home_html']()
    assert '<select name=assignee>' in page and 'Ops Bot' in page and '<th>Assignee</th>' in page
