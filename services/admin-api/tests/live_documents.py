"""Live documents for tests. People only ever save drafts (Publish and Promote make content live), so tests that need
a live document to start from write it through the store, as the test admin."""

from uuid import UUID

from knowledge_admin.documents import store as lifecycle
from knowledge_admin.documents.store import CONTENT, LINK_COLUMNS, Provenance
from test_repositories import HEADERS

DEFAULTS = {'description': '', 'purpose': 'explanation', 'audience': 'both', 'tags': []}


def admin(store, conn) -> UUID:
    """The principal the test HEADERS resolve to."""
    return store.actor(conn, HEADERS['X-Forwarded-User'], HEADERS['X-Forwarded-Email'])


def live_document(
    conn,
    project: UUID,
    slug: str,
    title: str,
    markdown: str,
    *,
    folder: str | None = None,
    actor: UUID | None = None,
    source_commits: dict | None = None,
    **fields,
) -> dict:
    """A new live document in the caller's transaction. (version 1, revision 1; the link fields in
    `fields` built-for). Returns {'id' (UUID), 'revision', 'version'}."""
    links = {field: [UUID(str(item)) for item in fields.pop(field, [])] for field in LINK_COLUMNS}
    content = {**DEFAULTS, **fields, 'title': title, 'markdown': markdown}
    snapshot = lifecycle.build_snapshot(conn, project, links, source_commits, None)
    return lifecycle.write_live(
        conn,
        project,
        Provenance('person', actor),
        document_id=None,
        slug=slug,
        folder=folder,
        content={key: content[key] for key in CONTENT},
        snapshot=snapshot,
        origin='manual',
        revision=None,
        links=links,
    )


def replace_live_document(
    conn, document_id, markdown: str, actor: UUID | None = None, *, links: dict | None = None, **content
) -> dict:
    """Replace a live document's text (and any other `content` field) in place, a new version, in the caller's
    transaction, as an internal writer does; `links` (link fields → ids) become its active links. Its drafts stay."""
    live = conn.execute('SELECT * FROM ka.documents WHERE id=%s AND NOT is_draft', (UUID(str(document_id)),)).fetchone()
    wanted = None
    snapshot = live['source_snapshot']
    if links is not None:
        wanted = {field: [UUID(str(item)) for item in links.get(field, [])] for field in LINK_COLUMNS}
        snapshot = lifecycle.build_snapshot(conn, live['project_id'], wanted, None, snapshot)
    return lifecycle.write_live(
        conn,
        live['project_id'],
        Provenance('person', actor),
        document_id=live['id'],
        slug=live['slug'],
        folder=live['folder'],
        content={**{key: live[key] for key in CONTENT}, **content, 'markdown': markdown},
        snapshot=snapshot,
        origin='manual',
        revision=live['revision'],
        links=wanted,
    )


def write_live_document(
    store,
    slug: str,
    title: str,
    markdown: str,
    *,
    folder: str | None = None,
    source_commits: dict | None = None,
    **fields,
) -> dict:
    """A new live document of the workspace shop, written as the test admin (version 1, revision 1; the
    link fields in `fields` built-for). Returns {'id' (str), 'revision', 'version'}."""
    with store.connect() as conn:
        saved = live_document(
            conn,
            store.project(conn, 'shop'),
            slug,
            title,
            markdown,
            folder=folder,
            actor=admin(store, conn),
            source_commits=source_commits,
            **fields,
        )
    return {**saved, 'id': str(saved['id'])}


def rewrite_live_document(store, document_id, markdown: str) -> dict:
    """Replace a live document's text in place (a new version) as the test admin; its drafts stay."""
    with store.connect() as conn:
        saved = replace_live_document(conn, document_id, markdown, admin(store, conn))
    return {**saved, 'id': str(saved['id'])}


def link_document(conn, project: UUID, document_id, column: str, resource_id, connection: str = 'linked') -> None:
    """Link a document to a resource (`column`: server_id, service_id, environment_id, code_project_id or
    deployment_id) in the caller's transaction, as a person does; an active link stays as it is."""
    if column not in LINK_COLUMNS.values():
        raise ValueError(column)
    conn.execute(
        f"""INSERT INTO ka.document_resources(project_id,document_id,{column},connection,added_how)
        VALUES (%s,%s,%s,%s,'person') ON CONFLICT DO NOTHING""",
        (project, UUID(str(document_id)), UUID(str(resource_id)), connection),
    )
