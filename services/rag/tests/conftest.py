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
from rag.collections import AREAS, Areas
from rag.workspace import Workspace
from rag.workspace_api import build_api

# --- PostgreSQL/pgvector workspace fixtures (collections API, sync worker) ---


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
    """(workspace, client, project, prefix, model_state, transport, area_roots)."""
    database = "test_" + uuid4().hex
    with psycopg.connect(workspace_postgres, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    roots = {area: tmp_path / area for area in AREAS}
    for root in roots.values():
        root.mkdir()
    workspace = Workspace(Areas(roots, {area: f"/srv/{area}" for area in AREAS}), workspace_postgres + f" dbname={database}", b"k" * 32)
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
    app = create_app()
    app.include_router(build_api(workspace, "test-key", transport))
    client = TestClient(app, headers={"x-rag-key": "test-key"})
    prefix = f"/backoffice/projects/{project}"
    assert client.get(prefix + "/settings").status_code == 200
    yield workspace, client, project, prefix, state, transport, roots
    client.close()
    with psycopg.connect(workspace_postgres, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
