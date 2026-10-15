"""The document lifecycle over ka.documents (live rows and drafts), ka.document_archive and ka.document_resources:
create and edit live documents, create, edit, promote and discard drafts, save links with provenance, and read
versions, links and a draft's outcome. Every function works inside the caller's transaction; the caller commits and
exports. This module never imports documents.api (api.py imports it)."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from knowledge_admin.db import audit

# Link field (LinksInput, a draft's resource_ids) → document_resources column. Fixed identifiers, never taken from a
# request.
LINK_COLUMNS = {
    'code_project_ids': 'code_project_id',
    'service_ids': 'service_id',
    'server_ids': 'server_id',
    'deployment_ids': 'deployment_id',
    'environment_ids': 'environment_id',
}
TYPE_LABEL = {
    'environment_id': 'environment',
    'service_id': 'service',
    'server_id': 'server',
    'code_project_id': 'code project',
    'deployment_id': 'deployment',
}
BUILT_FOR = "This document was built for {type} {name}; that link can't be removed."
CONTENT = ('title', 'description', 'purpose', 'audience', 'tags', 'markdown')

DOCUMENT_NOT_FOUND = 'Document not found'
DOCUMENT_CHANGED = 'This document changed. Reload it before saving; your text has not been overwritten.'
DRAFT_NOT_FOUND = 'Draft not found.'
DRAFT_CHANGED = 'This draft changed. Reload it before saving; your text has not been overwritten.'
STALE_BASE = 'STALE_BASE'
BASE_AHEAD = 'The base version is newer than the live version {live}. Reload the document.'
STALE_MESSAGE = (
    "This draft was based on version {base}; the live version is now {live}. Promoting replaces version {live}'s text."
)
NEW_DOCUMENT_REVISION = 'New documents cannot reuse a revision'
NAME_IN_USE = 'A document or draft named {slug} already exists in this folder. Choose another name.'
# An inventory document (documents.inventory): the one document of a server, service or environment, built for it.
# The database checks the same at commit (0107); these checks give the 409 first.
INVENTORY_TAKEN = '{type} {name} already has an inventory document.'
INVENTORY_BUILT_FOR = 'An inventory document must be built for one server, service or environment.'
INVENTORY_TYPES = {'server': 'Server', 'service': 'Service', 'environment': 'Environment'}
# Inventory entry kind → document_resources column (an entry's documents; deployments are not entries).
ENTRY_COLUMNS = {
    'server': 'server_id',
    'service': 'service_id',
    'environment': 'environment_id',
    'code_project': 'code_project_id',
}

# The snapshot key of each link field (documents/api.py builds on these; this module never imports api).
SNAPSHOT_KEYS = {
    'code_projects': 'code_project_ids',
    'services': 'service_ids',
    'servers': 'server_ids',
    'deployments': 'deployment_ids',
    'environments': 'environment_ids',
}

# A link row's resource id and display name (deployments: service / server / environment, as in snapshots).
RESOURCE_ID = 'COALESCE(r.environment_id,r.service_id,r.server_id,r.code_project_id,r.deployment_id)'
RESOURCE_NAME = """COALESCE(e.name,s.name,h.name,c.name,
    ds.name || ' / ' || dh.name || ' / ' || COALESCE(de.name,d.environment))"""
RESOURCE_JOINS = """LEFT JOIN ka.environments e ON e.id=r.environment_id
    LEFT JOIN ka.services s ON s.id=r.service_id LEFT JOIN ka.servers h ON h.id=r.server_id
    LEFT JOIN ka.repositories c ON c.id=r.code_project_id
    LEFT JOIN ka.service_deployments d ON d.id=r.deployment_id LEFT JOIN ka.services ds ON ds.id=d.service_id
    LEFT JOIN ka.servers dh ON dh.id=d.server_id LEFT JOIN ka.environments de ON de.id=d.environment_id
    LEFT JOIN ka.principals a ON a.id=r.added_by LEFT JOIN ka.principals rm ON rm.id=r.removed_by"""
LINK_ROW = f"""SELECT r.resource_type AS type,{RESOURCE_ID} AS id,{RESOURCE_NAME} AS name,r.connection,r.added_how,
    COALESCE(a.display_name,a.email) AS added_by,r.session_id,r.created_at{{extra}}
    FROM ka.document_resources r {RESOURCE_JOINS} WHERE r.document_id=%s"""


@dataclass(frozen=True)
class Provenance:
    """Who saves and how: a person, an agent run (session_id) or discovery."""

    how: str  # 'person' | 'agent_run' | 'discovery'
    actor: UUID | None
    session_id: UUID | None = None


def content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode('utf-8')).hexdigest()


def build_snapshot(
    conn, project: UUID, ids: dict[str, list[UUID]], source_commits: dict[str, str] | None, previous: dict | None
) -> dict:
    """The public inventory facts of the chosen resources (`ids`: link field → ids), read FOR SHARE. 400 when a
    resource is gone, a deployment lacks its service or server, or a source commit names an unselected code project.
    Without `source_commits`, the commits recorded in `previous` (the snapshot being replaced) carry over."""
    chosen = {field: list(ids.get(field) or []) for field in LINK_COLUMNS}
    # Select only public inventory columns; credentials never enter document history.
    sources = {
        'environments': conn.execute(
            'SELECT id,name FROM ka.environments WHERE project_id=%s AND id=ANY(%s) ORDER BY id FOR SHARE',
            (project, chosen['environment_ids']),
        ).fetchall(),
        'code_projects': conn.execute(
            'SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL AND id=ANY(%s) ORDER BY id FOR SHARE',
            (project, chosen['code_project_ids']),
        ).fetchall(),
        'services': conn.execute(
            'SELECT id,name FROM ka.services WHERE project_id=%s AND id=ANY(%s) ORDER BY id FOR SHARE',
            (project, chosen['service_ids']),
        ).fetchall(),
        'servers': conn.execute(
            'SELECT id,name,hostname FROM ka.servers WHERE project_id=%s AND id=ANY(%s) ORDER BY id FOR SHARE',
            (project, chosen['server_ids']),
        ).fetchall(),
        'deployments': conn.execute(
            """SELECT d.id,d.service_id,d.server_id,COALESCE(e.name,d.environment) AS environment,d.runtime_name,
            s.name || ' / ' || h.name || ' / ' || COALESCE(e.name,d.environment) AS name
            FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id JOIN ka.servers h ON h.id=d.server_id
            LEFT JOIN ka.environments e ON e.id=d.environment_id
            WHERE d.project_id=%s AND d.id=ANY(%s) ORDER BY d.id FOR SHARE OF d""",
            (project, chosen['deployment_ids']),
        ).fetchall(),
    }
    if any(len(sources[key]) != len(set(chosen[field])) for key, field in SNAPSHOT_KEYS.items()):
        raise HTTPException(
            400, 'A selected resource is no longer available. Reload resources and update the selection.'
        )
    if any(
        row['service_id'] not in chosen['service_ids'] or row['server_id'] not in chosen['server_ids']
        for row in sources['deployments']
    ):
        raise HTTPException(400, 'Select the service and server belonging to each selected deployment')
    snapshot = {
        key: [{k: str(v) if isinstance(v, UUID) else v for k, v in row.items()} for row in rows]
        for key, rows in sources.items()
    }
    if source_commits is not None:
        if not {str(key) for key in source_commits} <= {str(key) for key in chosen['code_project_ids']}:
            raise HTTPException(400, 'Source commits must belong to the selected code projects')
        commits = {str(key): commit for key, commit in source_commits.items()}
    elif previous is not None:
        commits = {row['id']: row['git_commit'] for row in previous.get('code_projects', []) if row.get('git_commit')}
    else:
        commits = {}
    for row in snapshot['code_projects']:
        if row['id'] in commits:
            row['git_commit'] = commits[row['id']]
    return snapshot


def _active_links(conn, document_id: UUID) -> dict[str, list[UUID]]:
    """The document's active links as link fields → ids."""
    rows = conn.execute(
        """SELECT environment_id,service_id,server_id,code_project_id,deployment_id FROM ka.document_resources
        WHERE document_id=%s AND removed_at IS NULL ORDER BY created_at,id""",
        (document_id,),
    ).fetchall()
    return {field: [row[column] for row in rows if row[column] is not None] for field, column in LINK_COLUMNS.items()}


def _usable(conn, project: UUID, ids: dict[str, list[UUID]]) -> dict[str, list[UUID]]:
    """The linked resources a snapshot can describe: archived code projects drop out, and a deployment stays only with
    its service and server (as drafts.available_resources)."""
    result = dict(ids)
    archived = {
        row['id']
        for row in conn.execute(
            'SELECT id FROM ka.repositories WHERE project_id=%s AND archived_at IS NOT NULL AND id=ANY(%s)',
            (project, ids['code_project_ids']),
        ).fetchall()
    }
    result['code_project_ids'] = [item for item in ids['code_project_ids'] if item not in archived]
    deployments = conn.execute(
        'SELECT id,service_id,server_id FROM ka.service_deployments WHERE project_id=%s AND id=ANY(%s)',
        (project, ids['deployment_ids']),
    ).fetchall()
    usable = {
        row['id']
        for row in deployments
        if row['service_id'] in ids['service_ids'] and row['server_id'] in ids['server_ids']
    }
    result['deployment_ids'] = [item for item in ids['deployment_ids'] if item in usable]
    return result


def save_links(
    conn,
    project: UUID,
    document_id: UUID,
    wanted: dict[str, list[UUID]],
    prov: Provenance,
    *,
    connection: str = 'linked',
) -> dict[str, list[str]]:
    """Make the document's active links of each field in `wanted` exactly those ids: unchanged rows stay, new ids get a
    row (`connection`, provenance), unwanted linked rows get removed_at/removed_by. Unwanting a built-for row is a 409
    and nothing is written. Fields missing from `wanted` stay as they are."""
    if connection not in ('built_for', 'linked'):
        raise ValueError(connection)
    conn.execute('SELECT id FROM ka.documents WHERE id=%s AND project_id=%s FOR UPDATE', (document_id, project))
    plan = []
    for field, column in LINK_COLUMNS.items():
        if field not in wanted:
            continue
        ids = list(dict.fromkeys(UUID(str(item)) for item in wanted[field] or []))
        active = conn.execute(
            f"""SELECT id,{column} AS resource_id,connection FROM ka.document_resources
            WHERE document_id=%s AND removed_at IS NULL AND {column} IS NOT NULL""",
            (document_id,),
        ).fetchall()
        current = {row['resource_id']: row for row in active}
        gone = [row for resource_id, row in current.items() if resource_id not in ids]
        locked = next((row for row in gone if row['connection'] == 'built_for'), None)
        if locked is not None:
            name = conn.execute(
                f'SELECT {RESOURCE_NAME} AS name FROM ka.document_resources r {RESOURCE_JOINS} WHERE r.id=%s',
                (locked['id'],),
            ).fetchone()['name']
            raise HTTPException(409, BUILT_FOR.format(type=TYPE_LABEL[column], name=name))
        plan.append((column, [item for item in ids if item not in current], gone))
    added, removed = [], []
    for column, new, gone in plan:
        for resource_id in new:
            conn.execute(
                f"""INSERT INTO ka.document_resources(project_id,document_id,{column},connection,added_how,added_by,
                session_id) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (project, document_id, resource_id, connection, prov.how, prov.actor, prov.session_id),
            )
            added.append(str(resource_id))
        for row in gone:
            conn.execute(
                'UPDATE ka.document_resources SET removed_at=now(),removed_by=%s WHERE id=%s', (prov.actor, row['id'])
            )
            removed.append(str(row['resource_id']))
    return {'added': added, 'removed': removed}


def _archive(conn, document_id: UUID, actor: UUID | None) -> UUID:
    """Copy the live row's current content into document_archive and move its sources there."""
    archive_id = conn.execute(
        """INSERT INTO ka.document_archive(id,project_id,document_id,version,title,description,purpose,audience,tags,
        markdown,content_hash,source_snapshot,origin,from_draft,session_id,created_by,created_at,archived_by)
        SELECT %s,project_id,id,version,title,description,purpose,audience,tags,markdown,content_hash,source_snapshot,
        origin,from_draft,session_id,version_created_by,version_created_at,%s FROM ka.documents WHERE id=%s
        RETURNING id""",
        (uuid4(), actor, document_id),
    ).fetchone()['id']
    conn.execute(
        'UPDATE ka.document_sources SET document_id=NULL,archive_id=%s WHERE document_id=%s', (archive_id, document_id)
    )
    return archive_id


def write_live(
    conn,
    project: UUID,
    prov: Provenance,
    *,
    document_id: UUID | None,
    slug: str,
    folder: str | None,
    content: dict,
    snapshot: dict,
    origin: str,
    revision: int | None,
    from_draft: bool = False,
    links: dict[str, list[UUID]] | None = None,
) -> dict:
    """Create a live document (version 1, `links` built-for) or save an existing one: a change of content, slug or
    snapshot archives the current content and bumps the version; the revision always bumps. An existing document keeps
    its folder. Returns {'id', 'revision', 'version'}."""
    # Serialize saves per project, as before, so the caller's export-folder check cannot race another save.
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'{project}:document-export',))
    values = [content[key] for key in CONTENT]
    if document_id is None:
        if revision is not None:
            raise HTTPException(400, NEW_DOCUMENT_REVISION)
        document_id = uuid4()
        conn.execute(
            """INSERT INTO ka.documents(id,project_id,slug,folder,revision,is_draft,version,title,description,purpose,
            audience,tags,markdown,content_hash,source_snapshot,origin,session_id,from_draft,version_created_at,
            version_created_by,created_by,updated_by)
            VALUES (%s,%s,%s,%s,1,false,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s)""",
            (
                document_id,
                project,
                slug,
                folder,
                *values,
                content_hash(content['markdown']),
                Jsonb(snapshot),
                origin,
                prov.session_id,
                from_draft,
                prov.actor,
                prov.actor,
                prov.actor,
            ),
        )
        if links:
            save_links(conn, project, document_id, links, prov, connection='built_for')
        audit(conn, project, prov.actor, 'document.created', 'document', document_id, {'version': 1})
        return {'id': document_id, 'revision': 1, 'version': 1}
    live = conn.execute(
        'SELECT * FROM ka.documents WHERE id=%s AND project_id=%s AND NOT is_draft FOR UPDATE', (document_id, project)
    ).fetchone()
    if live is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    if live['revision'] != revision:
        raise HTTPException(409, DOCUMENT_CHANGED)
    changed = (
        any(live[key] != content[key] for key in CONTENT) or live['slug'] != slug or live['source_snapshot'] != snapshot
    )
    version = live['version']
    if changed:
        _archive(conn, document_id, prov.actor)
        version += 1
        conn.execute(
            """UPDATE ka.documents SET slug=%s,title=%s,description=%s,purpose=%s,audience=%s,tags=%s,markdown=%s,
            content_hash=%s,source_snapshot=%s,version=%s,origin=%s,from_draft=%s,session_id=%s,
            version_created_at=now(),version_created_by=%s,updated_by=%s,revision=revision+1 WHERE id=%s""",
            (
                slug,
                *values,
                content_hash(content['markdown']),
                Jsonb(snapshot),
                version,
                origin,
                from_draft,
                prov.session_id,
                prov.actor,
                prov.actor,
                document_id,
            ),
        )
    else:
        conn.execute('UPDATE ka.documents SET updated_by=%s,revision=revision+1 WHERE id=%s', (prov.actor, document_id))
    saved = {'added': [], 'removed': []}
    if links is not None:
        saved = save_links(conn, project, document_id, links, prov, connection='linked')
    audit(
        conn,
        project,
        prov.actor,
        'document.updated',
        'document',
        document_id,
        {'version': version, 'links_added': saved['added'], 'links_removed': saved['removed']},
    )
    return {'id': document_id, 'revision': live['revision'] + 1, 'version': version}


def create_draft(
    conn,
    project: UUID,
    prov: Provenance,
    content: dict,
    *,
    origin: str,
    document_id: UUID | None = None,
    slug: str | None = None,
    folder: str | None = None,
    links: dict[str, list[UUID]] | None = None,
    base_version: int | None = None,
    inventory: bool = False,
) -> UUID:
    """Insert a draft: of the live document `document_id` (or the live document `folder` + `slug` name), from its
    live version (or `base_version`, the version the person loaded: no later than the live one, so the draft is stale
    when the document moved on), with its parent source; else a brand-new document with `slug`, `folder` and `links`
    as built-for, the inventory document of the resource it is built for when `inventory` (409 as claim_inventory). A
    draft of a live document has its document's flag, never its own (`inventory` is ignored); a new inventory draft
    whose folder and slug a live document uses is refused (409), never made a draft of it. A run's draft
    (prov.session_id) becomes the session's last_draft_id."""
    if document_id is None:
        named = conn.execute(
            """SELECT id FROM ka.documents WHERE project_id=%s AND NOT is_draft
            AND folder IS NOT DISTINCT FROM %s AND slug=%s""",
            (project, folder, slug),
        ).fetchone()
        if named is not None and inventory:
            raise HTTPException(409, NAME_IN_USE.format(slug=slug))
        if named is not None:
            document_id = named['id']
    base = None
    if document_id is not None:
        live = conn.execute(
            'SELECT version,title FROM ka.documents WHERE id=%s AND project_id=%s AND NOT is_draft',
            (document_id, project),
        ).fetchone()
        if live is None:
            raise HTTPException(404, DOCUMENT_NOT_FOUND)
        if base_version is not None and base_version > live['version']:
            raise HTTPException(400, BASE_AHEAD.format(live=live['version']))
        base, slug, folder = live['version'] if base_version is None else base_version, None, None
    draft_id = conn.execute(
        """INSERT INTO ka.documents(project_id,slug,folder,revision,is_draft,draft_of,base_version,inventory,title,
        description,purpose,audience,tags,markdown,content_hash,origin,session_id,created_by,updated_by)
        VALUES (%s,%s,%s,1,true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            project,
            slug,
            folder,
            document_id,
            base,
            inventory and document_id is None,
            *(content[key] for key in CONTENT),
            content_hash(content['markdown']),
            origin,
            prov.session_id,
            prov.actor,
            prov.actor,
        ),
    ).fetchone()['id']
    if document_id is not None:
        conn.execute(
            """INSERT INTO ka.document_sources(project_id,document_id,kind,source_document_id,source_title,
            source_version) VALUES (%s,%s,'parent',%s,%s,%s)""",
            (project, draft_id, document_id, live['title'], base),
        )
    else:
        if links:
            save_links(conn, project, draft_id, links, prov, connection='built_for')
        if inventory:
            claim_inventory(conn, draft_id)
    if prov.session_id is not None:
        conn.execute('UPDATE ka.agent_sessions SET last_draft_id=%s WHERE id=%s', (draft_id, prov.session_id))
    audit(
        conn,
        project,
        prov.actor,
        'document.draft.created',
        'document_draft',
        draft_id,
        {'document_id': document_id, 'origin': origin, 'session_id': prov.session_id},
    )
    return draft_id


def _locked_draft(conn, draft_id: UUID, revision: int | None, project: UUID | None = None) -> dict:
    """The draft row locked FOR UPDATE: 404 when gone (promoted or discarded), 409 when `revision` differs."""
    row = conn.execute(
        'SELECT * FROM ka.documents WHERE id=%s AND is_draft AND (%s::uuid IS NULL OR project_id=%s) FOR UPDATE',
        (draft_id, project, project),
    ).fetchone()
    if row is None:
        raise HTTPException(404, DRAFT_NOT_FOUND)
    if revision is not None and row['revision'] != revision:
        raise HTTPException(409, DRAFT_CHANGED)
    return row


def update_draft(conn, draft_id: UUID, actor: UUID | None, content: dict, *, revision: int | None, edited: bool) -> int:
    """Replace a draft's text; `edited` marks a person's edit (it stays marked). Returns the new revision."""
    row = _locked_draft(conn, draft_id, revision)
    conn.execute(
        """UPDATE ka.documents SET title=%s,description=%s,purpose=%s,audience=%s,tags=%s,markdown=%s,content_hash=%s,
        edited=edited OR %s,updated_by=%s,revision=revision+1 WHERE id=%s""",
        (*(content[key] for key in CONTENT), content_hash(content['markdown']), edited, actor, draft_id),
    )
    return row['revision'] + 1


def promote_draft(
    conn, project: UUID, actor: UUID | None, draft_id: UUID, *, revision: int, confirm_stale: bool
) -> dict:
    """Make a draft live. A draft of a live document: the live content is archived, the draft's content replaces it
    in place (same id, version + 1) and every draft of the document is deleted. A brand-new draft becomes the live
    document (version 1). Either way the inventory flag stays (the live row's, or the brand-new draft's own). A stale
    draft needs `confirm_stale`. Returns {'document_id', 'version', 'revision', 'deleted_drafts', 'new'}; the caller
    exports after commit."""
    # The live row before the draft: two promotes of drafts of one document queue on the live row, and the second
    # then finds its draft deleted (404) instead of deadlocking on the drafts the first deletes. FOR NO KEY UPDATE
    # (the live row's key never changes) lets a run's turn-end source refresh hold KEY SHARE on it through a
    # document_sources foreign key without a lock cycle.
    peek = conn.execute(
        'SELECT draft_of FROM ka.documents WHERE id=%s AND project_id=%s AND is_draft', (draft_id, project)
    ).fetchone()
    if peek is None:
        raise HTTPException(404, DRAFT_NOT_FOUND)
    live = None
    if peek['draft_of'] is not None:
        live = conn.execute(
            'SELECT * FROM ka.documents WHERE id=%s AND NOT is_draft FOR NO KEY UPDATE', (peek['draft_of'],)
        ).fetchone()
    draft = _locked_draft(conn, draft_id, revision, project)
    if draft['draft_of'] is None:
        snapshot = build_snapshot(conn, project, _usable(conn, project, _active_links(conn, draft_id)), None, None)
        conn.execute(
            """UPDATE ka.documents SET is_draft=false,version=1,from_draft=true,source_snapshot=%s,
            version_created_at=now(),version_created_by=%s,updated_by=%s,revision=revision+1 WHERE id=%s""",
            (Jsonb(snapshot), actor, actor, draft_id),
        )
        result = {
            'document_id': draft_id,
            'version': 1,
            'revision': draft['revision'] + 1,
            'deleted_drafts': 0,
            'new': True,
        }
        replaced = []
    else:
        if live is None or live['id'] != draft['draft_of']:  # the draft row never changes its document
            raise HTTPException(404, DOCUMENT_NOT_FOUND)
        if draft['base_version'] != live['version'] and not confirm_stale:
            raise HTTPException(
                409,
                {
                    'message': STALE_MESSAGE.format(base=draft['base_version'] or '?', live=live['version']),
                    'code': STALE_BASE,
                },
            )
        snapshot = build_snapshot(
            conn, project, _usable(conn, project, _active_links(conn, live['id'])), None, live['source_snapshot']
        )
        _archive(conn, live['id'], actor)
        conn.execute(
            """UPDATE ka.documents SET title=%s,description=%s,purpose=%s,audience=%s,tags=%s,markdown=%s,
            content_hash=%s,source_snapshot=%s,version=version+1,origin=%s,from_draft=true,session_id=%s,
            version_created_at=now(),version_created_by=%s,updated_by=%s,revision=revision+1 WHERE id=%s""",
            (
                *(draft[key] for key in CONTENT),
                draft['content_hash'],
                Jsonb(snapshot),
                draft['origin'],
                draft['session_id'],
                actor,
                actor,
                live['id'],
            ),
        )
        conn.execute('UPDATE ka.document_sources SET document_id=%s WHERE document_id=%s', (live['id'], draft_id))
        deleted = [
            row['id']
            for row in conn.execute('DELETE FROM ka.documents WHERE draft_of=%s RETURNING id', (live['id'],)).fetchall()
        ]
        replaced = [item for item in deleted if item != draft_id]
        result = {
            'document_id': live['id'],
            'version': live['version'] + 1,
            'revision': live['revision'] + 1,
            'deleted_drafts': len(deleted),
            'new': False,
        }
    audit(
        conn,
        project,
        actor,
        'document.draft.promoted',
        'document_draft',
        draft_id,
        {
            'document_id': result['document_id'],
            'version': result['version'],
            'stale_confirmed': confirm_stale,
            'deleted_drafts': result['deleted_drafts'],
        },
    )
    for other in replaced:  # the document's other drafts: their outcome is 'replaced'
        audit(
            conn,
            project,
            actor,
            'document.draft.replaced',
            'document_draft',
            other,
            {'document_id': result['document_id'], 'version': result['version']},
        )
    audit(
        conn,
        project,
        actor,
        'document.created' if result['new'] else 'document.updated',
        'document',
        result['document_id'],
        {'version': result['version'], 'from_draft': True},
    )
    return result


def discard_draft(conn, project: UUID, actor: UUID | None, draft_id: UUID, *, revision: int) -> None:
    """Delete a draft (a brand-new draft's links and sources go with it)."""
    draft = _locked_draft(conn, draft_id, revision, project)
    conn.execute('DELETE FROM ka.documents WHERE id=%s', (draft_id,))
    audit(
        conn,
        project,
        actor,
        'document.draft.discarded',
        'document_draft',
        draft_id,
        {'document_id': draft['draft_of']},
    )


VERSION_COLUMNS = """version,title,description,purpose,audience,tags,markdown,content_hash,source_snapshot,origin,
    from_draft,session_id"""


def versions(conn, document_id: UUID) -> list[dict]:
    """The document's versions, newest first: the live row (live) and its archive rows."""
    return conn.execute(
        """SELECT version,title,origin,version_created_at AS created_at,from_draft,true AS live FROM ka.documents
        WHERE id=%s AND NOT is_draft
        UNION ALL SELECT version,title,origin,created_at,from_draft,false FROM ka.document_archive WHERE document_id=%s
        ORDER BY version DESC""",
        (document_id, document_id),
    ).fetchall()


def version(conn, document_id: UUID, number: int) -> dict | None:
    """One version's content by number (the live row or an archive row), or None."""
    return conn.execute(
        f"""SELECT {VERSION_COLUMNS},version_created_by AS created_by,version_created_at AS created_at,true AS live
        FROM ka.documents WHERE id=%s AND NOT is_draft AND version=%s
        UNION ALL SELECT {VERSION_COLUMNS},created_by,created_at,false FROM ka.document_archive
        WHERE document_id=%s AND version=%s""",
        (document_id, number, document_id, number),
    ).fetchone()


def inventory_resource(conn, document_id: UUID) -> tuple[str, UUID] | None:
    """(type, id) of the one server, service or environment the document is built for, or None when it is built for
    none, for several, or for another kind of resource."""
    rows = conn.execute(
        """SELECT resource_type,COALESCE(server_id,service_id,environment_id) AS id FROM ka.document_resources
        WHERE document_id=%s AND connection='built_for' AND removed_at IS NULL""",
        (document_id,),
    ).fetchall()
    if len(rows) != 1 or rows[0]['resource_type'] not in INVENTORY_TYPES:
        return None
    return rows[0]['resource_type'], rows[0]['id']


def inventory_document_of(conn, kind: str, resource_id: UUID, *, other_than: UUID | None = None) -> UUID | None:
    """The id of the server's, service's or environment's inventory document (a live document or a brand-new draft,
    `other_than` left out), or None. Takes the resource's lock first, the one the database's commit-time check takes:
    a concurrent creation waits for this transaction and then sees its document."""
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'inventory-document:{resource_id}',))
    found = conn.execute(
        f"""SELECT d.id FROM ka.documents d JOIN ka.document_resources r ON r.document_id=d.id
        WHERE d.inventory AND d.draft_of IS NULL AND d.id IS DISTINCT FROM %s AND r.{ENTRY_COLUMNS[kind]}=%s
        AND r.connection='built_for' AND r.removed_at IS NULL LIMIT 1""",
        (other_than, resource_id),
    ).fetchone()
    return found['id'] if found else None


def claim_inventory(conn, document_id: UUID) -> None:
    """Before flagging `document_id` (a live document or a brand-new draft) as its resource's inventory document: 409
    unless it is built for exactly one server, service or environment that has no other inventory document (live or
    brand-new draft). Takes the resource's lock, the one the database's commit-time check takes: a concurrent claim
    waits for this transaction and then sees its document."""
    resource = inventory_resource(conn, document_id)
    if resource is None:
        raise HTTPException(409, INVENTORY_BUILT_FOR)
    kind, resource_id = resource
    if inventory_document_of(conn, kind, resource_id, other_than=document_id) is not None:
        name = conn.execute(f'SELECT name FROM ka.{kind}s WHERE id=%s', (resource_id,)).fetchone()['name']
        raise HTTPException(409, INVENTORY_TAKEN.format(type=INVENTORY_TYPES[kind], name=name))


def resource_documents(conn, project: UUID, kind: str, resource_id: UUID) -> dict:
    """The documents of an inventory entry from their links: its inventory document (flagged, built for it: {id, title,
    markdown, version, is_draft, published}, a live document or a brand-new draft (version null, published false), or
    None) and every other live document with an active link to it ({id, title, connection}), by title."""
    column = ENTRY_COLUMNS[kind]
    inventory = None
    if kind in INVENTORY_TYPES:
        inventory = conn.execute(
            f"""SELECT d.id,d.title,d.markdown,d.version,d.is_draft,NOT d.is_draft AS published FROM ka.documents d
            JOIN ka.document_resources r ON r.document_id=d.id
            WHERE d.project_id=%s AND d.draft_of IS NULL AND d.inventory AND r.{column}=%s AND r.connection='built_for'
            AND r.removed_at IS NULL""",
            (project, resource_id),
        ).fetchone()
    documents = conn.execute(
        f"""SELECT d.id,d.title,r.connection FROM ka.documents d JOIN ka.document_resources r ON r.document_id=d.id
        WHERE d.project_id=%s AND NOT d.is_draft AND r.{column}=%s AND r.removed_at IS NULL AND d.id IS DISTINCT FROM %s
        ORDER BY lower(d.title),d.id""",
        (project, resource_id, inventory['id'] if inventory else None),
    ).fetchall()
    return {'inventory_document': inventory, 'documents': documents}


def resource_links(conn, document_id: UUID) -> list[dict]:
    """The document's active links: type, id, name, connection, added_how, added_by, session_id, created_at."""
    return conn.execute(
        LINK_ROW.format(extra='') + ' AND r.removed_at IS NULL ORDER BY r.created_at,r.id', (document_id,)
    ).fetchall()


def resource_history(conn, document_id: UUID) -> list[dict]:
    """Every link row of the document, newest change (added or removed) first, with removed_at/removed_by."""
    return conn.execute(
        LINK_ROW.format(extra=',r.removed_at,COALESCE(rm.display_name,rm.email) AS removed_by')
        + ' ORDER BY COALESCE(r.removed_at,r.created_at) DESC,r.created_at DESC,r.id',
        (document_id,),
    ).fetchall()


# A draft's outcome as jsonb {id, document_id, status} for the draft id expression {draft}: open while its row exists
# (document_id: the live document it revises, or its own id when brand-new); else from the audit log: promoted or
# discarded (its own event), or replaced (its document.draft.replaced event, written by the promote of another draft
# of its document); NULL when unknown. The audit log is read (by resource, indexed: audit_events_document_draft) only
# when {draft} is set and its row is gone. The one definition behind draft_outcome and the sessions' document_draft
# (drafts.SESSION_DRAFT).
DRAFT_OUTCOME = """(SELECT CASE
        WHEN x.id IS NOT NULL THEN jsonb_build_object('id',x.id,'document_id',COALESCE(x.draft_of,x.id),'status','open')
        WHEN own.action IS NOT NULL THEN jsonb_build_object('id',{draft},'document_id',own.details->'document_id',
            'status',CASE own.action WHEN 'document.draft.promoted' THEN 'promoted'
                WHEN 'document.draft.replaced' THEN 'replaced' ELSE 'discarded' END)
        END
    FROM (SELECT 1) one LEFT JOIN ka.documents x ON x.id={draft} AND x.is_draft
    LEFT JOIN LATERAL (SELECT a.action,a.details FROM ka.audit_events a WHERE {draft} IS NOT NULL AND x.id IS NULL
        AND a.resource_type='document_draft' AND a.resource_id={draft}
        AND a.action IN ('document.draft.promoted','document.draft.discarded','document.draft.replaced')
        ORDER BY a.created_at DESC,a.id DESC LIMIT 1) own ON true)"""


def draft_outcome(conn, draft_id: UUID) -> dict | None:
    """{'id', 'document_id', 'status'} of a draft: open while its row exists, else promoted, discarded or replaced
    (another draft of its document was promoted) from the audit log; None when unknown."""
    row = conn.execute(
        f'SELECT {DRAFT_OUTCOME.format(draft="q.id")} AS outcome FROM (SELECT %s::uuid AS id) q', (draft_id,)
    ).fetchone()
    outcome = row['outcome']
    if outcome is None:
        return None
    return {
        'id': UUID(outcome['id']),
        'document_id': UUID(outcome['document_id']) if outcome['document_id'] else None,
        'status': outcome['status'],
    }
