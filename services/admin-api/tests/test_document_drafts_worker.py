"""A finished turn refreshes the sources of the run's working drafts: the documents the run read, at the version it read."""

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from knowledge_admin.documents.api import DocumentInput, save_document
from knowledge_admin.documents.drafts import NEW_DEFAULTS
from knowledge_admin.documents.store import Provenance, create_draft
from knowledge_admin.knowledge_agents import session_worker as shared
from test_generations import sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS


def runner(sessions) -> tuple[UUID, shared.SessionRunner]:
    sid = UUID(start(sessions)['id'])
    [row] = shared.claim(sessions.store, 'worker-1', [], 4)
    return sid, shared.SessionRunner(sessions.store, row)


def drafts_of(sessions, sid) -> list[dict]:
    with sessions.store.connect() as conn:
        return conn.execute(
            """SELECT id,draft_of,slug,folder,title,purpose,markdown,origin,edited,revision,base_version
            FROM ka.documents WHERE is_draft AND session_id=%s ORDER BY created_at,id""",
            (sid,),
        ).fetchall()


def tool_call(sessions, sid, turn: int, seq: int, name: str, arguments: dict, *, as_text: bool = False) -> None:
    """A recorded tool call. A native session records the arguments as an object (the real shape); as_text: a JSON
    string, as the SDK hands them over."""
    with sessions.store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,%s,'tool_call','ad-hoc',%s)""",
            (
                sessions.store.project(conn, 'shop'),
                f'cc:{sid}:{turn}',
                seq,
                Jsonb(
                    {
                        'name': name,
                        'call_id': f'call-{seq}',
                        'arguments': json.dumps(arguments) if as_text else arguments,
                    }
                ),
            ),
        )


def document(sessions, slug: str, folder: str | None, markdown: str) -> dict:
    with sessions.store.connect() as conn:
        return save_document(
            conn,
            sessions.store.project(conn, 'shop'),
            None,
            DocumentInput(slug=slug, title=slug.replace('-', ' ').title(), markdown=markdown),
            folder=folder,
        )


def test_sources_are_the_documents_the_run_read_at_the_version_it_read(sessions):
    alpha = document(sessions, 'alpha', 'team', '# Alpha v1')
    document(sessions, 'beta', None, '# Beta')
    document(sessions, 'gamma', 'team/deep', '# Gamma')
    sid, run = runner(sessions)
    tool_call(sessions, sid, 1, 1, 'get_document', {'document_id': str(alpha['id'])})
    tool_call(
        sessions,
        sid,
        1,
        2,
        'read_document',
        {'collection': 'documents', 'path': 'generated-documents/beta/README.md'},
        as_text=True,
    )
    tool_call(sessions, sid, 1, 3, 'read_document', {'collection': 'documents', 'path': 'team/deep/gamma/README.md'})
    tool_call(sessions, sid, 1, 4, 'read_document', {'collection': 'documents', 'path': 'team/alpha/notes.md'})
    tool_call(sessions, sid, 1, 5, 'get_document', {'document_id': 'not-a-uuid'})
    tool_call(sessions, sid, 1, 6, 'list_files', {'path': 'team/alpha/README.md'})
    with sessions.store.connect() as conn:  # alpha changes after the run read it
        save_document(
            conn,
            sessions.store.project(conn, 'shop'),
            None,
            DocumentInput(slug='alpha', title='Alpha', markdown='# Alpha v2', revision=1),
            alpha['id'],
        )
    with sessions.store.connect() as conn:  # the draft the run's save_document tool wrote
        create_draft(
            conn,
            sessions.store.project(conn, 'shop'),
            Provenance('agent_run', None, sid),
            {**NEW_DEFAULTS, 'title': 'Payments', 'markdown': '# Agent text'},
            origin='generated',
            slug='payments',
            folder='team',
        )
    run.finish_turn(1, 'waiting', '', usage=None)
    [draft] = drafts_of(sessions, sid)
    url = f'/api/documentation/drafts/{draft["id"]}'
    read = lambda: sorted(
        (s['kind'], s['source_title'], s['source_version'])
        for s in sessions.client.get(url, headers=HEADERS).json()['sources']
    )
    assert read() == [('read', 'Alpha', 1), ('read', 'Beta', 1), ('read', 'Gamma', 1)]
    tool_call(sessions, sid, 2, 1, 'get_document', {'document_id': str(alpha['id'])})  # read again, now at v2
    run.finish_turn(2, 'waiting', '', usage=None)
    assert read() == [('read', 'Alpha', 1), ('read', 'Beta', 1), ('read', 'Gamma', 1)]  # recomputed; first read kept
