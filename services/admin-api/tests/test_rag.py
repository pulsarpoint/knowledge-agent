import json
import time
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
        assert client.get("/api/rag/collections").status_code == 403
        assert client.get("/api/rag/collections", headers={"x-forwarded-groups": "docs-admins"}).status_code == 403
        assert client.get("/api/other/rag/collections", headers=HEADERS).status_code == 404
        collection = str(uuid4())
        for method, resource in (("GET", "tree"), ("POST", "folders"), ("POST", "jobs"), ("PUT", f"folders/{collection}"),
                                 ("DELETE", f"folders/{collection}"), ("GET", f"collections/{collection}/other"),
                                 ("POST", f"collections/{collection}/files"), ("DELETE", "collections"),
                                 ("PUT", "file"), ("DELETE", "file"), ("DELETE", "paths"),
                                 ("PUT", f"collections/{collection}/paths"), ("DELETE", f"collections/{collection}/paths"),
                                 ("PUT", "collections/by-name/jira"), ("PUT", "collections/by-name/jira/paths"),
                                 ("DELETE", "collections/by-name/jira/paths")):
            assert client.request(method, f"/api/rag/{resource}", headers=HEADERS).status_code == 404, resource
        assert calls == []
        with store.connect() as conn:
            project = store.project(conn, "docs")
        prefix = f"/backoffice/projects/{project}/"
        for method, resource in (("GET", "collections"), ("POST", "collections"), ("GET", f"collections/{collection}"),
                                 ("PUT", f"collections/{collection}"), ("DELETE", f"collections/{collection}"),
                                 ("POST", f"collections/{collection}/paths"),
                                 ("DELETE", f"collections/{collection}/paths/{collection}"),
                                 ("POST", f"collections/{collection}/sync"), ("POST", "collections/sync-stale"),
                                 ("GET", f"collections/{collection}/files"), ("GET", f"collections/{collection}/chunks"),
                                 ("GET", f"collections/{collection}/log"), ("GET", "folders"),
                                 ("POST", f"jobs/{collection}/cancel"), ("GET", "jobs")):
            body = {"json": {}} if method in ("POST", "PUT") else {}
            assert client.request(method, f"/api/rag/{resource}", headers=HEADERS, **body).status_code == 200, resource
            assert (calls[-1].method, calls[-1].url.path) == (method, prefix + resource)
        response = client.post("/api/rag/search", headers=HEADERS, json={"project_id": str(uuid4()), "query": "test"})
        assert response.status_code == 200
        assert json.loads(calls[-1].content)["project_id"] == str(project)
        assert calls[-1].url.path == "/search"
        assert calls[-1].headers["x-rag-key"] == "private-service-key"
        assert calls[-1].headers["x-rag-actor"]
        assert "private-service-key" not in response.text
        assert client.get("/api/rag/file?path=docs%2Ffile.md", headers=HEADERS).status_code == 200
        assert calls[-1].url.path == f"/backoffice/projects/{project}/file"
        assert calls[-1].url.params["path"] == "docs/file.md"
        assert client.put("/api/rag/settings", headers=HEADERS, content="not-json").status_code == 400


def test_startup_and_code_projects_never_call_rag(store, tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_BACKOFFICE_KEY", "private-service-key")
    root = tmp_path / "code"
    for folder in ("api", "shared"):
        (root / folder).mkdir(parents=True)
    monkeypatch.setenv("CODE_ROOT", str(root))
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={})
    cfg = Config(apps=[App(name="docs", app_root=None)], llm=None)
    server = ServerConfig(cors_origin=None, session_retention_days=90, state_dir=str(tmp_path))
    with httpx.Client(base_url="http://rag.test", transport=httpx.MockTransport(upstream)) as backend:
        with TestClient(create_app(cfg, server=server, repository_store=store, backoffice_client=backend)) as client:
            created = client.post("/api/code-projects", headers=HEADERS, json={
                "name": "Payments", "description": "", "remote_url": None, "folder_paths": ["api"]})
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]
            row = next(p for p in client.get("/api/code-projects", headers=HEADERS).json()["projects"] if p["id"] == project_id)
            assert not {"searchable", "collection", "search_error"} & set(row)
            edited = client.put(f"/api/code-projects/{project_id}", headers=HEADERS, json={
                "name": "Payments", "description": "", "remote_url": None, "folder_paths": ["api", "shared"],
                "revision": row["revision"]})
            assert edited.status_code == 200 and edited.json() == {"id": project_id}
            assert client.put(f"/api/code-projects/{project_id}/search", headers=HEADERS,
                              json={"enabled": True}).status_code in (404, 405)
            archived = client.post(f"/api/code-projects/{project_id}/archive", headers=HEADERS)
            assert archived.status_code == 200 and archived.json() == {"id": project_id}
            assert all(p["id"] != project_id for p in client.get("/api/code-projects", headers=HEADERS).json()["projects"])
            assert client.post(f"/api/code-projects/{project_id}/archive", headers=HEADERS).status_code == 404
            time.sleep(0.2)  # startup background tasks have had time to run
    assert calls == []
