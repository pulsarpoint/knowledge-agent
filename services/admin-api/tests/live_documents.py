"""Live documents for tests. People only ever save drafts (Publish and Promote make content live), so tests that need
a live document to start from write it through the store, as the test admin, and export it like a promote does."""

from uuid import UUID

from knowledge_admin.documents import store as lifecycle
from knowledge_admin.documents.api import export_args, export_document, export_input
from knowledge_admin.documents.store import CONTENT, LINK_COLUMNS, Provenance
from test_repositories import HEADERS

DEFAULTS = {'description': '', 'purpose': 'explanation', 'audience': 'both', 'tags': []}


def admin(store, conn) -> UUID:
    """The principal the test HEADERS resolve to."""
    return store.actor(conn, HEADERS['X-Forwarded-User'], HEADERS['X-Forwarded-Email'])


def _export(store, document_id: UUID) -> None:
    try:
        with store.connect() as conn:
            export_document(**export_args(export_input(conn, document_id)))
    except (OSError, ValueError):  # no DOCUMENT_OUTPUT_ROOT in this test: as a route, a failed export is ignored
        pass


def write_live_document(
    store,
    slug: str,
    title: str,
    markdown: str,
    *,
    folder: str | None = None,
    source_commits: dict | None = None,
    export: bool = True,
    **fields,
) -> dict:
    """A new live document (version 1, revision 1; the link fields in `fields` built-for). Returns {'id' (str),
    'revision', 'version'}."""
    links = {field: [UUID(str(item)) for item in fields.pop(field, [])] for field in LINK_COLUMNS}
    content = {**DEFAULTS, **fields, 'title': title, 'markdown': markdown}
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        snapshot = lifecycle.build_snapshot(conn, project, links, source_commits, None)
        saved = lifecycle.write_live(
            conn,
            project,
            Provenance('person', admin(store, conn)),
            document_id=None,
            slug=slug,
            folder=folder,
            content={key: content[key] for key in CONTENT},
            snapshot=snapshot,
            origin='manual',
            revision=None,
            links=links,
        )
    if export:
        _export(store, saved['id'])
    return {**saved, 'id': str(saved['id'])}


def rewrite_live_document(store, document_id, markdown: str) -> dict:
    """Replace a live document's text in place (a new version), as an internal writer does; its drafts stay."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        live = conn.execute(
            'SELECT * FROM ka.documents WHERE id=%s AND NOT is_draft', (UUID(str(document_id)),)
        ).fetchone()
        saved = lifecycle.write_live(
            conn,
            project,
            Provenance('person', admin(store, conn)),
            document_id=live['id'],
            slug=live['slug'],
            folder=live['folder'],
            content={**{key: live[key] for key in CONTENT}, 'markdown': markdown},
            snapshot=live['source_snapshot'],
            origin='manual',
            revision=live['revision'],
        )
    _export(store, saved['id'])
    return {**saved, 'id': str(saved['id'])}
