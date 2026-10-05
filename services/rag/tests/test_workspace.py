"""Real PostgreSQL/pgvector tests; model traffic stays inside MockTransport.

The `env` and `workspace_postgres` fixtures live in conftest.py; the helpers below
are shared by test_collections_api.py and test_sync.py.
"""
import base64
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from rag.collections import AREAS
from rag.workspace import Workspace
from rag.workspace_worker import claim, process


SETTINGS = ("description", "search_mode", "embeddings_enabled", "include", "exclude", "chunk_size", "chunk_overlap")


def update(env, collection_id, **changes):
    """PUT the collection's current search settings with `changes` applied."""
    client, prefix = env[1], env[3]
    row = client.get(prefix + f"/collections/{collection_id}").json()
    return client.put(prefix + f"/collections/{collection_id}",
                      json={**{key: row[key] for key in SETTINGS}, **changes, "revision": row["revision"]})


def collection(env, name="docs", *sources, **settings):
    """Ensure collection `name` holding `sources` as unowned paths (default rag-data/<name>), then apply settings."""
    client, prefix = env[1], env[3]
    for source in sources or (f"rag-data/{name}",):
        added = client.put(prefix + f"/collections/by-name/{name}/paths", json={"source_path": source})
        assert added.status_code == 200, added.text
    collection_id = added.json()["id"]
    if settings:
        saved = update(env, collection_id, **settings)
        assert saved.status_code == 200, saved.text
    return collection_id


def write(env, path="readme.md", content="# Payments\n\nRetries use exponential backoff.", name="docs"):
    """Create or replace rag-data/<name>/<path> through the rag-data writer."""
    client, prefix = env[1], env[3]
    full = f"rag-data/{name}/{path}"
    previous = client.get(prefix + "/file", params={"collection": name, "path": full})
    result = client.put(prefix + "/file", json={"path": full, "content": content,
                                                "expected_hash": previous.json()["hash"] if previous.status_code == 200 else None})
    assert result.status_code == 200, result.text


def sync(env, collection_id, **options):
    workspace, client, _, prefix, _, transport = env[:6]
    result = client.post(prefix + f"/collections/{collection_id}/sync", json=options)
    assert result.status_code == 202, result.text
    job = claim(workspace)
    assert job is not None
    stats = process(workspace, job, transport)
    return stats, client.get(prefix + f"/collections/{collection_id}").json()


def search(env, query="retries", collections=None, mode=None):
    client, project = env[1], env[2]
    body = {"project_id": str(project), "query": query}
    if collections is not None:
        body["collections"] = collections
    if mode is not None:
        body["mode"] = mode
    return client.post("/search", json=body)


def configure(env, **model):
    """Select the fixture embedding model; keyword arguments edit that model's row first."""
    workspace, client, project, prefix = env[:4]
    columns = {'embedding_model': 'model', 'embedding_version': 'embedding_version', 'embedding_dimensions': 'embedding_dimensions'}
    with workspace.connect() as conn:
        for field, column in columns.items():
            if field in model:
                conn.execute(sql.SQL('UPDATE ka.llm_models SET {}=%s WHERE id=%s').format(sql.Identifier(column)), (model.pop(field), project))
    assert not model, model
    config = client.get(prefix + "/settings").json()
    result = client.put(prefix + "/settings", json={"revision": config["revision"], "settings": config["settings"],
                                                    "embedding_model_id": str(project)})
    assert result.status_code == 200, result.text


def chunks(env, collection_id, path="rag-data/docs/readme.md"):
    client, prefix = env[1], env[3]
    return [row["text"] for row in client.get(prefix + f"/collections/{collection_id}/chunks", params={"path": path}).json()["chunks"]]


def test_single_workspace_and_cross_project_isolation(env):
    workspace, client = env[0], env[1]
    with workspace.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other','Other') RETURNING id").fetchone()["id"]
    assert client.get(f"/backoffice/projects/{other}/settings").status_code == 404
    assert client.get(f"/backoffice/projects/{other}/collections").status_code == 404
    assert client.put(f"/backoffice/projects/{other}/collections/by-name/x", json={}).status_code == 404
    with workspace.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.rag_workspaces").fetchone()["n"] == 1
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (other,))


def test_settings_report_areas_and_keep_only_the_file_limit(env):
    client, prefix = env[1], env[3]
    config = client.get(prefix + "/settings").json()
    assert config["settings"] == {"max_file_bytes": 2_000_000}
    assert "source_location" not in config and "source_root" not in config
    assert [a["area"] for a in config["areas"]] == list(AREAS)
    rag_data = next(a for a in config["areas"] if a["area"] == "rag-data")
    assert rag_data["host_root"] == "/srv/rag-data" and rag_data["container_root"] == str(env[6]["rag-data"])
    payload = {"revision": config["revision"], "settings": {"max_file_bytes": 5000}}
    assert client.put(prefix + "/settings", json={**payload, "areas": []}).status_code == 400
    for legacy in ({"chunk_size": 1000}, {"extensions": [".md"]}, {"embedding_enabled": True}):
        assert client.put(prefix + "/settings", json={**payload, "settings": {"max_file_bytes": 5000, **legacy}}).status_code == 400
    saved = client.put(prefix + "/settings", json=payload)
    assert saved.status_code == 200 and saved.json()["settings"] == {"max_file_bytes": 5000}
    assert saved.json()["revision"] > config["revision"]


def test_areas_come_from_deployment_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    for root_env, host_env in AREAS.values():
        monkeypatch.delenv(root_env, raising=False)
        monkeypatch.delenv(host_env, raising=False)
    monkeypatch.setenv("RAG_SOURCE_ROOT", str(tmp_path / "rag"))
    monkeypatch.setenv("RG_DATA", "/srv/knowledge sources")
    monkeypatch.setenv("CODE_ROOT", str(tmp_path / "code"))
    workspace = Workspace.from_env()
    assert workspace.areas.roots == {"rag-data": tmp_path / "rag", "code": tmp_path / "code"}
    assert workspace.areas.host_path("rag-data/jira") == "/srv/knowledge sources/jira"
    assert workspace.areas.host_path("code/payments") is None


def test_settings_select_shared_model_and_reject_inline_connection(env):
    workspace, client, project, prefix = env[:4]
    cfg = client.get(prefix + '/settings').json()
    assert client.post(prefix + '/test/embedding').status_code == 409  # no model selected yet
    payload = {'revision': cfg['revision'], 'settings': cfg['settings'], 'embedding_model_id': str(project)}
    assert client.put(prefix + '/settings', json={**payload, 'embedding_model_id': str(uuid4())}).status_code == 400
    assert client.put(prefix + '/settings', json={**payload, 'embedding_key': 'private-test-key'}).status_code == 400
    response = client.put(prefix + '/settings', json=payload)
    assert response.status_code == 200 and response.json()['embedding_model_id'] == str(project)
    assert 'fixture-key' not in response.text and 'has_embedding_key' not in response.json()
    with workspace.connect() as conn:
        assert workspace.embedding_model(conn, project).api_key == 'fixture-key'
    assert client.post(prefix + '/test/embedding').status_code == 200
    assert client.put(prefix + '/settings', json=payload).status_code == 409
    payload['revision'] = response.json()['revision']
    for field in ['embedding_base_url', 'source_root']:
        assert client.put(prefix + '/settings', json={**payload, 'settings': {**payload['settings'], field: '/etc'}}).status_code == 400
    with workspace.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET enabled=false WHERE id=%s', (project,))
    cfg = client.get(prefix + '/settings').json()
    assert cfg['revision'] > payload['revision']
    assert client.post(prefix + '/test/embedding').status_code == 409
    assert client.put(prefix + '/settings', json={**payload, 'revision': cfg['revision'], 'embedding_model_id': None}).status_code == 200


def test_authentication_is_required(env):
    client, project, prefix = env[1], env[2], env[3]
    assert client.get(prefix + "/collections", headers={"x-rag-key": "wrong"}).status_code == 403
    assert client.get(prefix + "/settings", headers={"x-rag-key": ""}).status_code == 403
    assert client.post("/search", headers={"x-rag-key": ""}, json={"project_id": str(project), "query": "x"}).status_code == 403


def test_documents_catalog_and_workspace_lookup(env):
    client, project, prefix, roots = env[1], env[2], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env, description="Operations documentation")
    write(env)
    sync(env, created)
    assert client.post('/rag', json={'project_id': str(project), 'query': 'retries'}).status_code == 404
    documents = client.get(prefix + '/documents').json()
    assert [(d['collection'], d['path'], d['collection_description']) for d in documents] == [("docs", "rag-data/docs/readme.md", "Operations documentation")]
    assert 0 < len(documents[0]['description']) <= 280
    assert client.get('/backoffice/workspace').json()['project_id'] == str(project)
