"""0130: a draft of a live document may hold links ('linked', never built_for) until it is published; down drops them
and forbids any link on such a draft again."""

from pathlib import Path

import psycopg
import pytest

from drafts_world import docs, new_draft  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0130_draft_links.up.sql', '0130_draft_links.down.sql'
LINK = """INSERT INTO ka.document_resources(project_id,document_id,environment_id,connection,added_how)
    VALUES (%s,%s,%s,%s,'agent_run')"""


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_a_draft_of_a_live_document_holds_linked_links_only_and_down_drops_them(docs):
    draft = new_draft(docs, document_id=docs.runbook)['id']
    with docs.store.connect() as conn:
        environment = conn.execute(
            "INSERT INTO ka.environments(project_id,name) VALUES (%s,'Production') RETURNING id", (docs.project,)
        ).fetchone()['id']
        conn.execute(LINK, (docs.project, draft, environment, 'linked'))
        with pytest.raises(psycopg.errors.RaiseException, match='Built-for links belong to the document'):
            with conn.transaction():
                conn.execute(LINK, (docs.project, draft, environment, 'built_for'))
        run(conn, DOWN)
        held = 'SELECT count(*) AS n FROM ka.document_resources WHERE document_id=%s'
        assert conn.execute(held, (draft,)).fetchone()['n'] == 0
        with pytest.raises(psycopg.errors.RaiseException, match='Links belong to the document, not to a draft of it'):
            with conn.transaction():
                conn.execute(LINK, (docs.project, draft, environment, 'linked'))
        run(conn, UP)
        conn.execute(LINK, (docs.project, draft, environment, 'linked'))
        assert conn.execute(held, (draft,)).fetchone()['n'] == 1
