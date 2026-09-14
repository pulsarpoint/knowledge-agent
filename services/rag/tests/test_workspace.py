"""Real PostgreSQL/pgvector tests; model traffic stays inside MockTransport."""
import base64
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from rag.api import create_app
from rag.config import Config
from rag.workspace import Workspace
from rag.workspace_api import build_api
from rag.workspace_worker import claim, process


@pytest.fixture(scope="session")
def workspace_postgres():
    name = "ka-rag-test-" + uuid4().hex[:12]
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-p", "127.0.0.1::5432", "--tmpfs", "/var/lib/postgresql/data", "knowledge-agent-v3-postgres:17.10-vector0.8.6"], check=True, capture_output=True)
    try:
        port = subprocess.check_output(["docker", "port", name, "5432"], text=True).strip().rsplit(":", 1)[1]
        info = f"host=127.0.0.1 port={port} user=postgres dbname=postgres connect_timeout=2"
        for _ in range(60):
            try:
                with psycopg.connect(info):
                    break
            except psycopg.OperationalError:
                time.sleep(.25)
        else:
            pytest.fail("PostgreSQL did not start")
        yield info
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def env(workspace_postgres, tmp_path):
    database = "test_" + uuid4().hex
    with psycopg.connect(workspace_postgres, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    workspace = Workspace(tmp_path / "sources", workspace_postgres + f" dbname={database}", b"k" * 32)
    with workspace.connect() as conn:
        for path in sorted((Path(__file__).resolve().parents[3] / "db/migrations").glob("*.up.sql")):
            conn.execute(path.read_text(), prepare=False)
        project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('test','Test') RETURNING id").fetchone()["id"]
        actor = conn.execute("INSERT INTO ka.principals(issuer,subject) VALUES ('test','test') RETURNING id").fetchone()['id']
        nonce = os.urandom(12)
        secret = nonce + workspace.cipher.encrypt(nonce, b'fixture-key', f'{project}:embedding'.encode())
        conn.execute("""INSERT INTO ka.llm_models(id,name,provider,model,api_format,base_url,
            embedding_dimensions,embedding_version,api_key_ciphertext,created_by,updated_by)
            VALUES (%s,'Fixture embeddings','openai','test','embeddings','http://model.test/v1',3,'1',%s,%s,%s)""",
            (project, secret, actor, actor))
    state = {"calls": [], "fail": False, "bad_dimension": False, "hook": None}
    def models(request):
        body = json.loads(request.content)
        state["calls"].append(body)
        if state["hook"]:
            state["hook"]()
        if state["fail"]:
            return httpx.Response(401, json={"error": {"message": "SECRET-token-do-not-leak"}})
        if request.url.path.endswith('/invoke'):
            assert request.url.host == 'bedrock-runtime.eu-central-1.amazonaws.com'
            assert request.headers['authorization'] == 'Bearer fixture-key'
            return httpx.Response(200, json={'embedding': [1.0] * body['dimensions']})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0] if state["bad_dimension"] else [1.0, float(len(text) % 7 + 1), 0.3]} for i, text in enumerate(body["input"])]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Payments retry safely [S1]."}}]})
    transport = httpx.MockTransport(models)
    app = create_app(Config(collections=[], llm=None))
    app.include_router(build_api(workspace, "test-key", transport))
    wrapped = create_app(Config(collections=[], llm=None), workspace=workspace, workspace_key="test-key")
    app.exception_handlers.update(wrapped.exception_handlers)
    client = TestClient(app, headers={"x-rag-key": "test-key"})
    prefix = f"/backoffice/projects/{project}"
    assert client.get(prefix + "/settings").status_code == 200
    yield workspace, client, project, prefix, state, transport
    client.close()
    with psycopg.connect(workspace_postgres, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


def folder(env, path="docs", description="Operations documentation"):
    _, client, _, prefix, _, _ = env
    revision = client.get(prefix + "/settings").json()["revision"]
    result = client.post(prefix + "/folders", json={"path": path, "description": description, "revision": revision})
    assert result.status_code == 200, result.text
    return result.json()["id"]


def write(env, path="docs/readme.md", content="# Payments\n\nRetries use exponential backoff."):
    _, client, _, prefix, _, _ = env
    previous = client.get(prefix + "/file", params={"path": path})
    result = client.put(prefix + "/file", json={"path": path, "content": content, "expected_hash": previous.json()["hash"] if previous.status_code == 200 else None})
    assert result.status_code == 200, result.text


def run(env, folder_id, **options):
    workspace, client, _, prefix, _, transport = env
    result = client.post(prefix + "/jobs", json={"folder_id": folder_id, **options})
    assert result.status_code == 202, result.text
    job = claim(workspace)
    assert job is not None
    stats = process(workspace, job, transport)
    return stats, client.get(prefix + "/jobs").json()["jobs"][0]


def configure(env, **settings):
    workspace, client, project, prefix, _, _ = env
    model_fields = {'embedding_model': 'model', 'embedding_version': 'embedding_version', 'embedding_dimensions': 'embedding_dimensions'}
    with workspace.connect() as conn:
        for field, column in model_fields.items():
            if field in settings:
                conn.execute(sql.SQL('UPDATE ka.llm_models SET {}=%s WHERE id=%s').format(sql.Identifier(column)), (settings.pop(field), project))
    config = client.get(prefix + "/settings").json()
    result = client.put(prefix + "/settings", json={"revision": config["revision"], "settings": {**config["settings"], **settings}, 'embedding_model_id': str(project)})
    assert result.status_code == 200, result.text


def search(env, query="retries", scope="*", mode="full_text"):
    _, client, project, _, _, _ = env
    return client.post("/search", json={"project_id": str(project), "query": query, "folder": scope, "mode": mode})


@pytest.mark.parametrize('mode', ['full_text', 'embedding', 'hybrid'])
def test_multiple_folder_search_scopes_before_ranking(env, mode):
    _, client, project, _, _, _ = env
    configure(env, embedding_enabled=True, embedding_model='test', embedding_dimensions=3)
    for path in ['payments', 'payments/runbooks', 'payments-old', 'people']:
        fid = folder(env, path, description='retries folder description')
        write(env, path + '/readme.md')
        run(env, fid)
    payload = {'project_id': str(project), 'query': 'retries', 'folders': ['payments', 'people'], 'top': 30, 'mode': mode}
    response = client.post('/search', json=payload)
    assert response.status_code == 200, response.text
    assert {hit['folder'] for hit in response.json()['results']} == {'payments', 'people'}
    response = client.post('/search', json={**payload, 'folders': ['payments.*', 'payments.runbooks']})
    hits = response.json()['results']
    assert {hit['folder'] for hit in hits} == {'payments', 'payments.runbooks'}
    assert len({hit['id'] for hit in hits}) == len(hits)
    assert {hit['kind'] for hit in hits} == {'file', 'folder'}
    # A small top must still return evidence when most indexed files are excluded.
    response = client.post('/search', json={**payload, 'folders': ['people'], 'top': 1})
    assert [hit['folder'] for hit in response.json()['results']] == ['people']
    assert client.post('/search', json={**payload, 'folders': []}).status_code == 400
    assert client.post('/search', json={**payload, 'folder': 'payments'}).status_code == 400


def test_skips_all_processing_and_publishes_deletions(env, monkeypatch):
    fid = folder(env)
    write(env)
    write(env, "docs/other.txt", "Another original document")
    first, job = run(env, fid)
    assert job["status"] == "succeeded", job
    assert first["parsed_files"] == 2
    hit = search(env).json()["results"][0]
    from rag import workspace_worker
    parser = workspace_worker.parse_source
    monkeypatch.setattr(workspace_worker, "parse_source", lambda *a: pytest.fail("Unchanged files must not be parsed"))
    second, _ = run(env, fid)
    assert second["skipped_files"] == 2 and second["parsed_files"] == 0
    assert search(env).json()["results"][0]["id"] == hit["id"]
    monkeypatch.setattr(workspace_worker, "parse_source", parser)
    write(env, content="# Payments\n\nRetries have a new policy.")
    _, client, _, prefix, _, _ = env
    document = client.get(prefix + "/file", params={"path": "docs/other.txt"}).json()
    assert client.delete(prefix + "/file", params={"path": document["path"], "expected_hash": document["hash"]}).status_code == 200
    third, _ = run(env, fid)
    assert third["parsed_files"] == 1 and third["removed_entries"] == 1
    assert search(env, "original").json()["results"] == []
    assert "new policy" in search(env).json()["results"][0]["text"]


def test_scopes_descriptions_and_single_workspace_root(env):
    parent = folder(env, "docs", "Parent inventory")
    folder(env, "docs/payments", "Payments reconciliation directory")
    folder(env, "docs/payments_extra", "Neighbor directory")
    write(env, "docs/payments/retry.txt", "subfolderonlyneedle retries")
    write(env, "docs/payments_extra/retry.txt", "neighboronlyneedle retries")
    run(env, parent)
    assert search(env, "subfolderonlyneedle", "docs").json()["results"] == []
    assert search(env, "subfolderonlyneedle", "docs.*").json()["results"]
    assert search(env, "neighboronlyneedle", "docs.payments.*").json()["results"] == []
    assert search(env, "reconciliation", "docs.payments").json()["results"][0]["kind"] == "folder"
    workspace, client, _, _, _, _ = env
    with workspace.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other','Other') RETURNING id").fetchone()["id"]
    assert client.get(f"/backoffice/projects/{other}/settings").status_code == 404
    assert client.get(f"/backoffice/projects/{other}/tree").status_code == 404
    assert client.post("/search", json={"project_id": str(other), "query": "retries"}).status_code == 404
    assert (workspace.root / "docs/payments/retry.txt").is_file()
    assert not (workspace.root / str(other)).exists()
    with workspace.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.rag_workspaces").fetchone()["n"] == 1
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (other,))


def test_embedding_reuse_model_changes_and_atomic_failure(env):
    fid = folder(env)
    write(env)
    configure(env, embedding_enabled=True, embedding_model="test", embedding_dimensions=3)
    stats, job = run(env, fid)
    assert job["status"] == "succeeded", job
    assert stats["embedded_chunks"] >= 2
    assert search(env, mode="embedding").json()["results"]
    state = env[4]
    state["calls"].clear()
    stats, _ = run(env, fid)
    assert stats["embedded_chunks"] == 0 and state["calls"] == []
    write(env, "docs/copy.md")
    stats, _ = run(env, fid)
    assert stats["parsed_files"] == 1 and stats["embedded_chunks"] == 0 and stats["reused_vectors"] > 0
    configure(env, embedding_version="2")
    stats, _ = run(env, fid)
    assert stats["parsed_files"] == 0 and stats["embedded_chunks"] > 0
    write(env, content="# Changed\n\nReplacementneedle must not publish on failure.")
    state["fail"] = True
    stats, failed = run(env, fid)
    assert stats is None and failed["status"] == "failed"
    assert "SECRET" not in failed["error"]
    assert search(env, "Replacementneedle").json()["results"] == []
    assert search(env, "retries").json()["results"]
    state["fail"] = False
    stats, _ = run(env, fid)
    assert stats["parsed_files"] == 1
    assert search(env, "Replacementneedle").json()["results"]


@pytest.mark.parametrize("host_root", [None, Path("/srv/knowledge sources")])
def test_source_location_matches_the_application_and_is_read_only(env, host_root):
    workspace, client, project, prefix, _, _ = env
    workspace.host_root = host_root
    config = client.get(prefix + "/settings").json()
    tree = client.get(prefix + "/tree").json()
    expected = {
        "container_root": str(workspace.root),
        "host_root": str(host_root) if host_root is not None else None,
    }
    assert config["source_location"] == tree["source_location"] == expected
    assert config["source_root"] == tree["source_root"] == str(workspace.root)
    payload = {"revision": config["revision"], "settings": config["settings"], "source_location": {"host_root": "/etc"}}
    assert client.put(prefix + "/settings", json=payload).status_code == 400
    payload.pop("source_location")
    assert client.put(prefix + "/settings", json=payload).json()["source_location"] == expected


def test_source_host_path_comes_from_deployment_without_container_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    monkeypatch.setenv("RG_DATA", "/srv/knowledge sources")
    workspace = Workspace.from_env()
    assert workspace.host_root == Path("/srv/knowledge sources")
    assert workspace.root == tmp_path.resolve()
    monkeypatch.setenv("RG_DATA", "relative/path")
    with pytest.raises(ValueError, match="absolute Docker host path"):
        Workspace.from_env()
    monkeypatch.delenv("RG_DATA")
    assert Workspace.from_env().host_root is None


def test_settings_select_shared_model_and_reject_inline_connection(env):
    workspace, client, project, prefix, _, _ = env
    cfg = client.get(prefix + '/settings').json()
    payload = {'revision': cfg['revision'], 'settings': {**cfg['settings'], 'embedding_enabled': True}, 'embedding_model_id': str(project)}
    assert client.put(prefix + '/settings', json={**payload, 'embedding_model_id': None}).status_code == 400
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
    payload['revision'] = cfg['revision']
    assert client.put(prefix + '/settings', json=payload).status_code == 400
    assert client.post(prefix + '/test/embedding').status_code == 409
    assert search(env, mode='embedding').status_code == 409
    assert search(env, mode='full_text').status_code == 200
    payload['settings']['embedding_enabled'] = False
    assert client.put(prefix + '/settings', json=payload).status_code == 200


def test_model_edit_during_processing_cannot_publish_stale_vectors(env):
    fid = folder(env)
    write(env)
    configure(env, embedding_enabled=True)
    workspace, _, project, _, state, _ = env
    def edit_model():
        with workspace.connect() as conn:
            conn.execute("UPDATE ka.llm_models SET embedding_version='2' WHERE id=%s", (project,))
    state['hook'] = edit_model
    stats, job = run(env, fid)
    assert stats is None and job['status'] == 'failed'
    assert not search(env).json()['results']


@pytest.mark.parametrize("path", ["../outside", "/absolute", "a/../b", "a.b", "a//b", "a\\b"])
def test_folder_traversal_rejected(env, path):
    assert env[1].post(env[3] + "/folders", json={"path": path, "revision": 1}).status_code == 400


def test_symlinks_and_stale_file_writes_rejected(env, tmp_path):
    folder(env)
    write(env)
    workspace, client, project, prefix, _, _ = env
    assert client.put(prefix + "/file", json={"path": "docs/readme.md", "content": "stale overwrite"}).status_code == 409
    assert client.delete(prefix + "/file", params={"path": "docs/readme.md", "expected_hash": "wrong"}).status_code == 409
    secret = tmp_path / "outside.txt"
    secret.write_text("outside-secret")
    (workspace.root / "docs" / "link.txt").symlink_to(secret)
    response = client.get(prefix + "/file", params={"path": "docs/link.txt"})
    assert response.status_code == 400 and "outside-secret" not in response.text
    assert client.get(prefix + "/tree").status_code == 400


def test_cancel_and_external_edit_cannot_publish(env):
    fid = folder(env)
    write(env)
    run(env, fid)
    configure(env, embedding_enabled=True, embedding_model="test", embedding_dimensions=3)
    workspace, client, project, prefix, state, _ = env
    state["hook"] = lambda: (workspace.root / "docs/readme.md").write_text("Changed during processing")
    stats, job = run(env, fid)
    assert stats is None and job["status"] == "failed"
    assert search(env, "retries").json()["results"]
    state["hook"] = None
    response = client.post(prefix + "/jobs", json={"folder_id": fid})
    job = claim(workspace)
    assert client.post(prefix + f"/jobs/{response.json()['id']}/cancel").status_code == 200
    assert process(workspace, job, env[-1]) is None
    assert search(env, "retries").json()["results"]


def test_empty_replacement_requires_intent_and_force_reprocesses(env):
    fid = folder(env)
    write(env)
    run(env, fid)
    stats, _ = run(env, fid, force=True)
    assert stats["parsed_files"] == 1
    workspace, _, project, _, _, _ = env
    (workspace.root / "docs/readme.md").unlink()
    stats, job = run(env, fid)
    assert stats is None and job["status"] == "failed"
    assert search(env).json()["results"]
    stats, _ = run(env, fid, allow_empty=True)
    assert stats["removed_entries"] == 1
    assert search(env).json()["results"] == []


def test_bad_embeddings_and_retrieval_catalog(env):
    fid = folder(env)
    write(env)
    configure(env, embedding_enabled=True, embedding_model="test", embedding_dimensions=3)
    _, client, project, _, state, _ = env
    state["bad_dimension"] = True
    stats, job = run(env, fid)
    assert stats is None and "dimensions" in job["error"]
    state["bad_dimension"] = False
    run(env, fid)
    assert client.post('/rag', json={'project_id': str(project), 'query': 'retries'}).status_code == 404
    prefix = f'/backoffice/projects/{project}'
    documents = client.get(prefix + '/documents').json()
    assert documents and documents[0]['path']
    assert 0 < len(documents[0]['description']) <= 280
    assert client.get('/backoffice/workspace').json()['project_id'] == str(project)


def test_authentication_and_queue_conflict(env):
    fid = folder(env)
    _, client, project, prefix, _, _ = env
    assert client.get(prefix + "/tree", headers={"x-rag-key": "wrong"}).status_code == 403
    assert client.post("/search", headers={"x-rag-key": ""}, json={"project_id": str(project), "query": "x"}).status_code == 403
    assert client.post(prefix + "/jobs", json={"folder_id": fid}).status_code == 202
    assert client.post(prefix + "/jobs", json={"folder_id": fid}).status_code == 409


def test_markdown_title_is_searchable_and_existing_folder_is_not_overwritten(env):
    fid = folder(env)
    write(env, content="# UniqueTitleNeedle\n\nOrdinary body text.")
    run(env, fid)
    assert search(env, "UniqueTitleNeedle").json()["results"]
    _, client, _, prefix, _, _ = env
    revision = client.get(prefix + "/settings").json()["revision"]
    assert client.post(prefix + "/folders", json={"path": "docs", "description": "overwrite", "revision": revision}).status_code == 409
    assert client.get(prefix + "/tree").json()["folders"][0]["description"] == "Operations documentation"


def delete(env, folder_id, revision=None):
    _, client, _, prefix, _, _ = env
    if revision is None:
        revision = client.get(prefix + "/settings").json()["revision"]
    return client.delete(prefix + f"/folders/{folder_id}", params={"revision": revision, "confirmed": True})


def test_recursive_deletion_purges_text_vectors_and_descriptions_but_preserves_neighbors(env):
    root = folder(env, "docs")
    target = folder(env, "docs/payments", "deleteddescriptionneedle")
    folder(env, "docs/payments/retries", "descendantdescriptionneedle")
    neighbor = folder(env, "docs/payments_extra", "neighbor description")
    write(env, "docs/payments/unique.md", "# Delete\n\ndeleteduniqueneedle")
    write(env, "docs/payments/retries/shared.txt", "sharedembeddingneedle")
    write(env, "docs/payments_extra/keep.txt", "sharedembeddingneedle")
    workspace, client, project, prefix, _, _ = env
    configure(env, embedding_enabled=True, embedding_model="test", embedding_dimensions=3)
    run(env, root)
    with workspace.connect() as conn:
        unique_hash = conn.execute("SELECT input_hash FROM ka.rag_chunks WHERE text LIKE '%deleteduniqueneedle%'").fetchone()["input_hash"]
        shared_hash = conn.execute("SELECT input_hash FROM ka.rag_chunks WHERE text='sharedembeddingneedle' LIMIT 1").fetchone()["input_hash"]
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other','Other') RETURNING id").fetchone()["id"]
    assert client.get(f"/backoffice/projects/{other}/settings").status_code == 404
    # Directly added and unsupported files must be removed too.
    (workspace.root / "docs/payments/unused.bin").write_bytes(b"\x00\x01")
    response = delete(env, target)
    assert response.status_code == 200, response.text
    assert response.json()["deleted_folders"] == 2 and response.json()["cleanup_pending"] is False
    assert not (workspace.root / "docs/payments").exists()
    assert (workspace.root / "docs/payments_extra/keep.txt").is_file()
    tree = client.get(prefix + "/tree").json()
    assert {f["path"] for f in tree["folders"]} == {"docs", "docs/payments_extra"}
    assert search(env, "deleteduniqueneedle").json()["results"] == []
    assert search(env, "deleteddescriptionneedle").json()["results"] == []
    assert search(env, "sharedembeddingneedle", "docs.payments.*", "embedding").json()["results"] == []
    assert search(env, "sharedembeddingneedle", "docs.payments_extra", "embedding").json()["results"]
    with workspace.connect() as conn:
        assert conn.execute("SELECT 1 FROM ka.rag_files WHERE folder_id IN (SELECT id FROM ka.rag_folders WHERE deleted_at IS NOT NULL)").fetchone() is None
        assert conn.execute("SELECT 1 FROM ka.rag_folders WHERE deleted_at IS NOT NULL AND description<>''").fetchone() is None
        assert conn.execute("SELECT 1 FROM ka.rag_vectors WHERE project_id=%s AND input_hash=%s", (project, unique_hash)).fetchone() is None
        assert conn.execute("SELECT 1 FROM ka.rag_vectors WHERE project_id=%s AND input_hash=%s", (project, shared_hash)).fetchone()
    assert client.post(prefix + "/jobs", json={"folder_id": target}).status_code == 404
    new_target = folder(env, "docs/payments", "New folder identity")
    assert new_target != target
    assert client.post(prefix + "/jobs", json={"folder_id": target}).status_code == 404
    assert delete(env, neighbor).status_code == 200
    assert delete(env, new_target).status_code == 200
    assert delete(env, root).status_code == 200
    with workspace.connect() as conn:
        for table in ("rag_files", "rag_chunks", "rag_vectors"):
            assert conn.execute(sql.SQL("SELECT count(*) AS n FROM ka.{} WHERE project_id=%s").format(sql.Identifier(table)), (project,)).fetchone()["n"] == 0


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize("job_scope", ["parent", "self", "child"])
def test_deletion_cancels_overlapping_jobs_and_prevents_republication(env, status, job_scope):
    root = folder(env, "docs")
    target = folder(env, "docs/payments")
    child = folder(env, "docs/payments/sub")
    write(env, "docs/payments/sub/test.txt", "deleteduniqueneedle")
    run(env, root)
    workspace, client, project, prefix, _, transport = env
    queued = client.post(prefix + "/jobs", json={"folder_id": {"parent": root, "self": target, "child": child}[job_scope]}).json()
    job = claim(workspace) if status == "running" else None
    assert delete(env, target).status_code == 200
    recorded = next(j for j in client.get(prefix + "/jobs").json()["jobs"] if j["id"] == queued["id"])
    assert recorded["status"] == "cancelled"
    if job is not None:
        assert process(workspace, job, transport) is None
    else:
        assert claim(workspace) is None
    assert search(env, "deleteduniqueneedle").json()["results"] == []
    assert not (workspace.root / "docs/payments").exists()


def test_deletion_during_embedding_cannot_restore_chunks(env):
    target = folder(env)
    write(env)
    run(env, target)
    configure(env, embedding_enabled=True, embedding_model="test", embedding_dimensions=3)
    state = env[4]
    def remove_during_request():
        state["hook"] = None
        assert delete(env, target).status_code == 200
    state["hook"] = remove_during_request
    stats, job = run(env, target)
    assert stats is None and job["status"] == "cancelled"
    assert search(env).json()["results"] == []
    with env[0].connect() as conn:
        assert conn.execute("SELECT 1 FROM ka.rag_chunks").fetchone() is None
        assert conn.execute("SELECT 1 FROM ka.rag_vectors").fetchone() is None


@pytest.mark.parametrize("failure", [PermissionError, FileNotFoundError])
def test_interrupted_source_cleanup_is_durable_and_never_rediscovered(env, monkeypatch, failure):
    target = folder(env)
    write(env)
    run(env, target)
    workspace, client, project, prefix, _, _ = env
    from rag import workspace as workspace_module
    original = workspace_module.shutil.rmtree
    def broken_removal(*args, **kwargs):
        raise failure("simulated interrupted filesystem cleanup")
    monkeypatch.setattr(workspace_module.shutil, "rmtree", broken_removal)
    try:
        response = delete(env, target)
        assert response.status_code == 202 and response.json()["cleanup_pending"]
        tree = client.get(prefix + "/tree").json()
        assert tree["folders"] == [] and tree["files"] == [] and tree["pending_deletions"] == ["docs"]
        assert search(env).json()["results"] == []
        assert client.get(prefix + "/file", params={"path": "docs/readme.md"}).status_code == 409
        assert client.put(prefix + "/file", json={"path": "docs/new.txt", "content": "must not be written"}).status_code == 409
        revision = client.get(prefix + "/settings").json()["revision"]
        assert client.post(prefix + "/folders", json={"path": "docs/sub", "revision": revision}).status_code == 409
    finally:
        monkeypatch.setattr(workspace_module.shutil, "rmtree", original)
    restarted = Workspace(workspace.root, workspace.conninfo, b"k" * 32)
    assert restarted.cleanup_deleted_folders(project) is False
    assert not (workspace.root / "docs").exists()
    assert client.get(prefix + "/tree").json()["pending_deletions"] == []


def test_deletion_does_not_follow_symlinks(env, tmp_path):
    target = folder(env)
    write(env)
    run(env, target)
    workspace, _, project, _, _, _ = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("Must survive")
    (workspace.root / "docs/link").symlink_to(outside, target_is_directory=True)
    assert delete(env, target).status_code == 200
    assert (outside / "keep.txt").read_text() == "Must survive"


def test_stale_or_cross_project_deletion_cannot_remove_data(env):
    target = folder(env)
    write(env)
    run(env, target)
    workspace, client, project, prefix, _, _ = env
    assert delete(env, target, revision=1).status_code == 409
    assert client.delete(prefix + f"/folders/{target}", params={"revision": 2}, headers={"x-rag-key": "wrong"}).status_code == 403
    with workspace.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other','Other') RETURNING id").fetchone()["id"]
    assert client.delete(f"/backoffice/projects/{other}/folders/{target}", params={"revision": 1, "confirmed": True}).status_code == 404
    assert (workspace.root / "docs/readme.md").exists()
    assert search(env).json()["results"]


def test_database_failure_rolls_back_index_deletion_and_keeps_source_files(env):
    target = folder(env)
    write(env)
    run(env, target)
    workspace, _, project, _, _, _ = env
    with workspace.connect() as conn:
        conn.execute("""CREATE FUNCTION ka.reject_folder_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
                       BEGIN RAISE EXCEPTION 'simulated database failure'; END; $$;
                       CREATE TRIGGER reject_folder_deletion BEFORE UPDATE ON ka.rag_folders
                       FOR EACH ROW WHEN (NEW.deleted_at IS NOT NULL) EXECUTE FUNCTION ka.reject_folder_deletion();""", prepare=False)
    assert delete(env, target).status_code == 503
    assert (workspace.root / "docs/readme.md").exists()
    assert search(env).json()["results"]


def test_recursive_deletion_requires_explicit_confirmation(env):
    target = folder(env)
    write(env)
    workspace, client, project, prefix, _, _ = env
    revision = client.get(prefix + "/settings").json()["revision"]
    assert client.delete(prefix + f"/folders/{target}", params={"revision": revision}).status_code == 400
    assert (workspace.root / "docs/readme.md").is_file()


def test_bedrock_embedding_processing_search_reuse_and_region_profile(env):
    from dataclasses import replace
    from rag.workspace_models import embedding_profile
    workspace, client, project, prefix, state, _ = env
    with workspace.connect() as conn:
        conn.execute("""UPDATE ka.llm_models SET provider='bedrock',model='amazon.titan-embed-text-v2:0',
            base_url=NULL,aws_region='eu-central-1',embedding_dimensions=256 WHERE id=%s""", (project,))
        model = workspace.embedding_model(conn, project)
        assert embedding_profile(model) != embedding_profile(replace(model, aws_region='us-east-1'))
        assert embedding_profile(model) != embedding_profile(replace(model, embedding_dimensions=512))
    configure(env, embedding_enabled=True)
    fid = folder(env)
    write(env)
    assert client.post(prefix + '/test/embedding').status_code == 200
    stats, job = run(env, fid)
    assert job['status'] == 'succeeded' and stats['embedded_chunks'] >= 2
    result = search(env, mode='embedding')
    assert result.status_code == 200 and result.json()['results']
    assert all(call['dimensions'] == 256 and call['normalize'] for call in state['calls'])
    state['calls'].clear()
    stats, _ = run(env, fid)
    assert stats['parsed_files'] == stats['embedded_chunks'] == 0 and not state['calls']
    state['fail'] = True
    result = search(env, mode='embedding')
    assert result.status_code == 502 and 'SECRET' not in result.text
    assert search(env, mode='full_text').status_code == 200
