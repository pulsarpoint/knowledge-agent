"""An Ask agent run (a revision session) on the document Runbook, for tool tests (a fixture other modules import)."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from drafts_world import docs, save_live  # noqa: F401  (fixture)
from knowledge_admin.capabilities import inventory
from knowledge_admin.tools.catalog import CATALOG
from session_rows import a_model

RUNBOOK = '''# Runbook

Intro.

## Backup

Old steps.

### Details

```bash
# not a heading
```

## Restore

Steps.
'''


@pytest.fixture
def rev(docs, monkeypatch):
    monkeypatch.setattr(inventory, '_store', docs.store)
    save_live(docs, docs.runbook, RUNBOOK)  # version 2
    sid = uuid4()
    with docs.store.connect() as conn:
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,target_document_id,agent_config) VALUES (%s,%s,NULL,%s,%s,'Revise','{}','{}','revision',%s,%s)""",
            (sid, docs.project, actor, a_model(conn, actor), docs.runbook, Jsonb({})))
    config = {'project_id': str(docs.project), 'revision': {'session_id': str(sid), 'document_id': docs.runbook}}
    return SimpleNamespace(docs=docs, sid=sid, config=config)


def call(rev, tool: str, **arguments) -> str:
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': rev.config}, spec.arguments.model_validate(arguments)))


def run_drafts(rev) -> list[dict]:
    with rev.docs.store.connect() as conn:
        return conn.execute('''SELECT id,document_id,status,edited,origin,markdown,title,description,revision,base_version_id
            FROM ka.document_drafts WHERE session_id=%s ORDER BY created_at,id''', (rev.sid,)).fetchall()
