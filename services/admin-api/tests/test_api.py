import httpx
import pytest
from fastapi.testclient import TestClient

from knowledge_admin.config import App, Config, ServerConfig
from knowledge_admin.app import create_app


def _client(tmp_path, **kwargs):
    cfg = Config(apps=[App(name="shop")])
    server = ServerConfig()
    return TestClient(create_app(cfg, server=server, **kwargs))


@pytest.mark.parametrize("groups", ["", "readers", "docs-admins-other", "other-docs-admins"])
def test_admin_session_requires_exact_admin_group(tmp_path, groups):
    assert _client(tmp_path).get("/api/session", headers={"X-Forwarded-Groups": groups}).status_code == 403


def test_admin_discovery_and_health(tmp_path):
    client = _client(tmp_path)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/session", headers={"X-Forwarded-Groups": "readers, docs-admins"}).json() == {"is_admin": True}


@pytest.mark.parametrize("names", [[], ["docs", "another"]])
def test_administration_requires_one_workspace(tmp_path, names):
    cfg = Config(apps=[App(name=name) for name in names])
    server = ServerConfig()
    with pytest.raises(ValueError, match="exactly one configured workspace"):
        create_app(cfg, server=server)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/toc"), ("GET", "/api/doc?path=index.md"),
    ("GET", "/api/search?q=example"), ("POST", "/api/chat"),
    ("GET", "/api/chats"), ("DELETE", "/api/chats/k1"),
])
def test_admin_api_does_not_mount_reader_routes(tmp_path, method, path):
    assert _client(tmp_path).request(method, path, headers={"X-Forwarded-Groups": "docs-admins"}).status_code == 404


def test_admin_audit_unknown_app_404(tmp_path):
    response = _client(tmp_path).get(
        "/api/ghost/admin/audit",
        headers={"X-Forwarded-Groups": "docs-admins"})
    assert response.status_code == 404


def test_admin_audit_has_no_application_prefixed_routes(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/ghost/admin/audit").status_code == 404
    assert client.get("/api/ghost/admin/audit",
                      headers={"X-Forwarded-Groups": "devs"}).status_code == 404


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/admin/backoffice/init"),
    ("GET", "/api/admin/backoffice/init/stream"),
    ("GET", "/api/admin/backoffice/status"),
])
def test_retired_initialization_routes_do_not_reach_rag(tmp_path, method, path):
    def unexpected_request(request):
        pytest.fail("Retired initialization route contacted RAG")

    with httpx.Client(base_url="http://rag.test", transport=httpx.MockTransport(unexpected_request)) as upstream:
        client = _client(tmp_path, backoffice_client=upstream)
        assert client.request(method, path, headers={"X-Forwarded-Groups": "docs-admins"}).status_code == 404


@pytest.mark.parametrize('groups', ['', 'readers', 'docs-admins-other'])
def test_admin_api_requires_exact_admin_group(tmp_path, groups):
    headers = {'X-Forwarded-Groups': groups, 'X-Forwarded-User': 'admin'}
    assert _client(tmp_path).get('/api/agents', headers=headers).status_code == 403


@pytest.mark.parametrize('method,path', [('GET', '/api/agents/search/status'), ('GET', '/api/agents/search/context'),
    ('POST', '/api/agents/search/chat'), ('GET', '/api/agents/search/chats')])
def test_legacy_search_chat_is_removed(tmp_path, method, path):
    assert _client(tmp_path).request(method, path, headers={'X-Forwarded-Groups': 'docs-admins'}).status_code in (404, 405)


@pytest.mark.parametrize("path", ["/api/agent-board/tasks", "/api/agent-board/settings"])
def test_the_multi_agent_board_is_gone(tmp_path, path):
    # Without storage the board answered 503; with the board removed the route does not exist.
    assert _client(tmp_path).get(path, headers={"X-Forwarded-Groups": "docs-admins"}).status_code == 404
