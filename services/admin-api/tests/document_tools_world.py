"""An Ask agent run (a revision session) on the document Runbook, running its turn 1, for tool tests (a fixture other
modules import)."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from access_helpers import with_access
from psycopg.types.json import Jsonb

from drafts_world import docs, save_live  # noqa: F401  (fixture)
from knowledge_admin.capabilities import inventory
from knowledge_admin.tools.catalog import CATALOG
from session_rows import a_model

RUNBOOK = """# Runbook

Intro.

## Backup

Old steps.

### Details

```bash
# not a heading
```

## Restore

Steps.
"""


@pytest.fixture
def rev(docs, monkeypatch):
    monkeypatch.setattr(inventory, '_store', docs.store)
    save_live(docs, docs.runbook, RUNBOOK)  # version 2
    sid = uuid4()
    with docs.store.connect() as conn:
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,target_document_id,agent_config,turns) VALUES (%s,%s,NULL,%s,%s,'Revise','{}','{}','revision',%s,%s,1)""",
            (sid, docs.project, actor, a_model(conn, actor), docs.runbook, Jsonb({})),
        )
    config = {
        'project_id': str(docs.project),
        'session_id': str(sid),
        'document_access': [{'id': str(docs.runbook), 'title': 'Runbook', 'editable': True}],
    }
    with_access(config, content_editor=True)  # a revision run writes its document
    return SimpleNamespace(docs=docs, sid=sid, config=config)


def call(rev, tool: str, **arguments) -> str:
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': rev.config}, spec.arguments.model_validate(arguments)))


def run_drafts(rev) -> list[dict]:
    with rev.docs.store.connect() as conn:
        return conn.execute(
            """SELECT id,document_id,edited,origin,markdown,title,description,revision,base_version
            FROM ka.document_drafts_view WHERE session_id=%s ORDER BY created_at,id""",
            (rev.sid,),
        ).fetchall()
