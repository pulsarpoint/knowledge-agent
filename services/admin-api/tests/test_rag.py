import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from docs_wiki.config import App, Config, ServerConfig
from knowledge_admin.app import create_app


HEADERS = {"x-forwarded-groups": "docs-admins", "x-forwarded-user": "admin-subject", "x-forwarded-email": "admin@example.test"}


def test_rag_proxy_auth_project_binding_and_allowlist(store, tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_BACKOFFICE_KEY", "private-service-key")
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"results": []})
    cfg = Config(apps=[App(name="docs", app_root=None)], llm=None)
    server = ServerConfig(cors_origin=None, session_retention_days=90, state_dir=str(tmp_path))
    with httpx.Client(base_url="http://rag.test", transport=httpx.MockTransport(upstream)) as backend:
        client = TestClient(create_app(cfg, server=server, repository_store=store, backoffice_client=backend))
        assert client.get("/api/rag/tree").status_code == 403
        assert client.get("/api/rag/tree", headers={"x-forwarded-groups": "docs-admins"}).status_code == 403
        assert client.get("/api/other/rag/tree", headers=HEADERS).status_code == 404
        assert client.get("/api/rag/collections", headers=HEADERS).status_code == 404
        assert calls == []
        response = client.post("/api/rag/search", headers=HEADERS, json={"project_id": str(uuid4()), "query": "test"})
        assert response.status_code == 200
        with store.connect() as conn:
            project = store.project(conn, "docs")
        assert json.loads(calls[-1].content)["project_id"] == str(project)
        assert calls[-1].url.path == "/search"
        assert calls[-1].headers["x-rag-key"] == "private-service-key"
        assert calls[-1].headers["x-rag-actor"]
        assert "private-service-key" not in response.text
        assert client.get("/api/rag/file?path=docs%2Ffile.md", headers=HEADERS).status_code == 200
        assert calls[-1].url.path == f"/backoffice/projects/{project}/file"
        assert calls[-1].url.params["path"] == "docs/file.md"
        assert client.put("/api/rag/settings", headers=HEADERS, content="not-json").status_code == 400
