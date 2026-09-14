import httpx
import pytest
from fastapi.testclient import TestClient

from docs_wiki import audit
from docs_wiki.config import App, Config, ServerConfig
from knowledge_admin.app import create_app


def _client(tmp_path, **kwargs):
    cfg = Config(apps=[App(name="shop", app_root=None)], llm=None)
    server = ServerConfig(cors_origin=None, session_retention_days=90, state_dir=str(tmp_path / "state"))
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
    cfg = Config(apps=[App(name=name, app_root=None) for name in names], llm=None)
    server = ServerConfig(cors_origin=None, session_retention_days=90, state_dir=str(tmp_path))
    with pytest.raises(ValueError, match="exactly one configured workspace"):
        create_app(cfg, server=server)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/toc"), ("GET", "/api/doc?path=index.md"),
    ("GET", "/api/search?q=example"), ("POST", "/api/chat"),
    ("GET", "/api/chats"), ("DELETE", "/api/chats/k1"),
])
def test_admin_api_does_not_mount_reader_routes(tmp_path, method, path):
    assert _client(tmp_path).request(method, path, headers={"X-Forwarded-Groups": "docs-admins"}).status_code == 404


def test_admin_audit_requires_group(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/admin/audit").status_code == 403
    wrong = client.get("/api/admin/audit",
                       headers={"X-Forwarded-Groups": "devs,qa"})
    assert wrong.status_code == 403
    ok = client.get("/api/admin/audit",
                    headers={"X-Forwarded-Groups": "devs, docs-admins"})
    assert ok.status_code == 200
    assert ok.json() == {"rows": []}


def test_admin_audit_returns_rows_and_filters(tmp_path):
    client = _client(tmp_path)
    state_dir = tmp_path / "state" / "shop"
    audit.append(state_dir, user_email="a@x.io", session_key="k1",
                 question="q1", answer="a1", sources_used=["docs"],
                 not_covered=False, escalated=False)
    audit.append(state_dir, user_email="b@x.io", session_key="k2",
                 question="q2", answer="a2", sources_used=[],
                 not_covered=True, escalated=False)
    headers = {"X-Forwarded-Groups": "docs-admins"}
    rows = client.get("/api/admin/audit", headers=headers).json()["rows"]
    assert [r["question"] for r in rows] == ["q2", "q1"]
    only_a = client.get("/api/admin/audit", params={"user": "a@x.io"},
                        headers=headers).json()["rows"]
    assert [r["user_email"] for r in only_a] == ["a@x.io"]
    by_session = client.get("/api/admin/audit/session/k1",
                            headers=headers).json()["rows"]
    assert [r["question"] for r in by_session] == ["q1"]
    assert client.get("/api/admin/audit/session/k1").status_code == 403


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


def test_admin_audit_users_and_sessions_endpoints(tmp_path):
    client = _client(tmp_path)
    state_dir = tmp_path / "state" / "shop"
    audit.append(state_dir, user_email="a@x.io", session_key="s1",
                 question="q1", answer="a1", sources_used=[],
                 not_covered=False, escalated=False)
    audit.append(state_dir, user_email="a@x.io", session_key="s1",
                 question="q2", answer="a2", sources_used=[],
                 not_covered=False, escalated=False)
    headers = {"X-Forwarded-Groups": "docs-admins"}
    assert client.get("/api/admin/audit/users").status_code == 403
    users = client.get("/api/admin/audit/users",
                       headers=headers).json()["users"]
    assert users == [{"user_email": "a@x.io", "sessions": 1,
                      "questions": 2, "last_ts": users[0]["last_ts"]}]
    assert client.get(
        "/api/admin/audit/users/a@x.io/sessions").status_code == 403
    sessions = client.get("/api/admin/audit/users/a@x.io/sessions",
                          headers=headers).json()["sessions"]
    assert sessions[0]["session_key"] == "s1"
    assert sessions[0]["first_question"] == "q1"
    assert sessions[0]["questions"] == 2
    # Application-prefixed routes no longer exist.
    assert client.get("/api/ghost/admin/audit/users").status_code == 404


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
def test_agent_test_requires_exact_admin_group(tmp_path, groups):
    client = _client(tmp_path)
    headers = {'X-Forwarded-Groups': groups, 'X-Forwarded-User': 'admin'}
    assert client.get('/api/agents/search/status', headers=headers).status_code == 403
    assert client.get('/api/agents/search/context', headers=headers).status_code == 403
    assert client.post('/api/agents/search/chat', headers=headers, json={'question': 'hello'}).status_code == 403


def test_admin_agent_status_is_explicit_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv('DOCS_WIKI_CHAT_MODEL', raising=False)
    response = _client(tmp_path).get('/api/agents/search/status', headers={'X-Forwarded-Groups': 'docs-admins'})
    assert response.status_code == 200
    assert response.json()['ask_enabled'] is True
