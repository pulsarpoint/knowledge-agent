"""The embedding migration preserves encrypted keys and existing vector signatures."""
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from rag.collections import DataRoot
from rag.workspace import Workspace, digest
from rag.workspace_models import embedding_profile


@pytest.mark.parametrize('configured,key_saved', [(True, True), (True, False), (False, True), (False, False)])
def test_migrate_saved_embedding_model(workspace_postgres, tmp_path, configured, key_saved):
    database = 'migration_' + uuid4().hex
    with psycopg.connect(workspace_postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    workspace = Workspace(DataRoot(None), workspace_postgres + f' dbname={database}', b'k' * 32)
    try:
        migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
        with workspace.connect() as conn:
            for path in sorted(migrations.glob('*.up.sql')):
                if int(path.name.split('_')[0]) < 37:
                    conn.execute(path.read_text(), prepare=False)
            project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('migration','Migration') RETURNING id").fetchone()['id']
            settings = {'chunk_size': 1000, 'embedding_enabled': configured, 'embedding_base_url': 'https://model.test/v1',
                        'embedding_model': 'existing-embedding-model' if configured else '', 'embedding_dimensions': 3, 'embedding_version': '3'}
            nonce = os.urandom(12)
            secret = nonce + workspace.cipher.encrypt(nonce, b'migration-key', f'{project}:embedding'.encode()) if key_saved else None
            conn.execute('INSERT INTO ka.rag_workspaces(project_id,settings,embedding_secret) VALUES (%s,%s,%s)', (project, Jsonb(settings), secret))
            conn.execute((migrations / '0037_embedding_models.up.sql').read_text(), prepare=False)
            row = workspace.settings(conn, project)
            assert row['settings'] == {'chunk_size': 1000, 'embedding_enabled': configured}
            assert 'embedding_secret' not in row
            if configured or key_saved:
                model = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (row['embedding_model_id'],)).fetchone()
                assert model['api_key_ciphertext'] == secret
                assert model['enabled'] is configured
                from knowledge_llm import decode_model
                decoded = decode_model(model, workspace.cipher)
                assert decoded.api_key == ('migration-key' if key_saved else None)
                if configured:
                    expected = digest({'url': settings['embedding_base_url'], 'model': settings['embedding_model'],
                                       'version': settings['embedding_version'], 'dimensions': settings['embedding_dimensions'], 'input_format': 'text-v1'})
                    assert embedding_profile(decoded) == expected
            else:
                assert row['embedding_model_id'] is None
                assert conn.execute('SELECT count(*) AS n FROM ka.llm_models').fetchone()['n'] == 0
        if configured or key_saved:
            with pytest.raises(psycopg.errors.RaiseException, match='rollback would lose'):
                with workspace.connect() as conn:
                    conn.execute((migrations / '0037_embedding_models.down.sql').read_text(), prepare=False)
    finally:
        with psycopg.connect(workspace_postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
