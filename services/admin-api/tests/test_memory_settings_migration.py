"""0132: the memory embedding model starts as the RAG workspace's model; memory search keeps using it."""

from pathlib import Path
from uuid import uuid4

from knowledge_admin.memory.embedding import current_model
from test_api import _client  # noqa: F401
from test_embedding_models import EMBEDDING
from test_llm import HEADERS, create

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0132_memory_settings.up.sql', '0132_memory_settings.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def workspace(conn, project, model_id):
    conn.execute(
        "INSERT INTO ka.rag_workspaces(project_id,settings,embedding_model_id) VALUES (%s,'{}',%s)"
        ' ON CONFLICT (project_id) DO UPDATE SET embedding_model_id=EXCLUDED.embedding_model_id',
        (project, model_id),
    )


def test_up_copies_the_workspace_model_and_search_still_uses_it(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', __import__('base64').urlsafe_b64encode(b'k' * 32).decode())
    model = create(_client(tmp_path, repository_store=store), EMBEDDING)
    with store.connect() as conn:
        shop = store.project(conn, f'shop-{uuid4().hex[:6]}')
        bare = store.project(conn, f'bare-{uuid4().hex[:6]}')
        run(conn, DOWN)
        workspace(conn, shop, model['id'])
        run(conn, UP)
        rows = conn.execute('SELECT project_id,embedding_model_id FROM ka.memory_settings').fetchall()
        assert [(r['project_id'], str(r['embedding_model_id'])) for r in rows] == [(shop, model['id'])]
        conn.execute('UPDATE ka.rag_workspaces SET embedding_model_id=NULL')
        assert str(current_model(conn, shop).id) == model['id'] and current_model(conn, bare) is None


def test_down_gives_the_model_back_to_the_workspace_and_drops_the_table(store, tmp_path):
    model = create(_client(tmp_path, repository_store=store), EMBEDDING)
    with store.connect() as conn:
        shop = store.project(conn, f'shop-{uuid4().hex[:6]}')
        conn.execute(
            'INSERT INTO ka.memory_settings(project_id,embedding_model_id) VALUES (%s,%s)', (shop, model['id'])
        )
        workspace(conn, shop, None)
        run(conn, DOWN)
        row = conn.execute('SELECT embedding_model_id FROM ka.rag_workspaces WHERE project_id=%s', (shop,)).fetchone()
        assert str(row['embedding_model_id']) == model['id']
        assert conn.execute("SELECT to_regclass('ka.memory_settings') AS t").fetchone()['t'] is None


def test_deleting_the_model_clears_the_setting(store, tmp_path):
    model = create(_client(tmp_path, repository_store=store), EMBEDDING)
    with store.connect() as conn:
        shop = store.project(conn, f'shop-{uuid4().hex[:6]}')
        conn.execute(
            'INSERT INTO ka.memory_settings(project_id,embedding_model_id) VALUES (%s,%s)', (shop, model['id'])
        )
        conn.execute('DELETE FROM ka.llm_models WHERE id=%s', (model['id'],))
        row = conn.execute('SELECT embedding_model_id FROM ka.memory_settings WHERE project_id=%s', (shop,)).fetchone()
    assert row['embedding_model_id'] is None
