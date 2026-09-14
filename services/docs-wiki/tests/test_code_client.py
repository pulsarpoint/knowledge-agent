import json

import httpx
import pytest

from docs_wiki.code_client import CodeAgentClient, CodeAgentError


def test_inventory_and_plain_todo_transport():
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.path == '/repos':
            return httpx.Response(200, json=[{'name': 'my repo', 'path_exists': True}])
        return httpx.Response(200, text='TODO')
    client = CodeAgentClient('http://code.test', transport=httpx.MockTransport(respond))
    assert client.repos()[0]['name'] == 'my repo'
    assert client.ask('my repo', 'Inspect', context='Some context') == 'TODO'
    assert calls[-1].url.raw_path == b'/repos/my%20repo/ask'
    assert json.loads(calls[-1].content) == {'question': 'Inspect', 'context': 'Some context'}
    client.close()


@pytest.mark.parametrize('method', ['repos', 'ask'])
@pytest.mark.parametrize('network_error', [False, True])
def test_failures_remain_errors(method, network_error):
    def fail(request):
        if network_error:
            raise httpx.ConnectError('connection refused', request=request)
        return httpx.Response(403, json={'error': {'message': 'outside scope'}})
    client = CodeAgentClient('http://code.test', transport=httpx.MockTransport(fail))
    with pytest.raises(CodeAgentError, match='connection refused' if network_error else 'outside scope'):
        getattr(client, method)(*(['repo', 'Inspect'] if method == 'ask' else []))
    client.close()
