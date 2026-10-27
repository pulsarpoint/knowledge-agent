"""The document tools. Every agent write to a document is a draft in the real documents, which a person publishes on
the document's page: search_documents finds published documents by words (full-text, drafts never); list_documents
lists a resource's documents with the run's drafts; get_document reads a document
(the run's open draft of it when it has one, else the live version); new_document creates a new document as a draft
built for a server, service or environment; update_document (the whole text, optionally linking a resource) and
edit_document (one exact passage) save the run's draft of a document.

A document is usable (read and drafted) when it is in the run's access object (config['access']) or linked to a
resource in it; a new document needs its resource in the access object. Checked by check_<tool> here, pure functions
over the access object (access.py); there is no role. The run's session is config['session_id'], set by the worker.

Document ids: a live document's id; a new document's id is the id of its draft (new_document returns it), which only
the run that created it uses until a person publishes the draft, and which stays the document's id once published
(store.promote_draft keeps the row). The run's own draft of a live document is taken for that document (a person
mentions a draft in the chat by its id): get_document reads, and update_document and edit_document save, the run's
draft of it.

A save locks the run's session, then its draft (the order the worker and promote take them in), never ka.projects or
ka.documents rows of live documents, and recomputes the draft's sources from the run's tool calls."""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import HTTPException
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.inventory import connect, dumps, project_of
from knowledge_admin.documents.api import Description, Markdown, Tags, Title, document_slug
from knowledge_admin.documents.drafts import NEW_DEFAULTS, audit, free_slug, live_content, record_run_sources
from knowledge_admin.documents.store import (
    CONTENT,
    ENTRY_COLUMNS,
    Provenance,
    add_link,
    create_draft,
    description_short,
    draft_outcome,
    resource_document_list,
    update_draft,
)
from knowledge_admin.tools import texts
from knowledge_admin.tools.registry import tool

NO_SESSION = 'The document tools work only in an agent session.'
NAME_RACE = 'Other documents took this name at the same time. Call new_document again.'
MAX_MARKDOWN = 50_000
MAX_LISTED = 100
DOCUMENT_GONE = 'This document no longer exists.'
CLOSED = 'This run is closed.'
NOT_FOUND = 'This text is not in the document. Read it again with get_document.'
MANY = 'This text appears {n} times. Include more of the surrounding text.'
NO_HEADING = 'No heading named "{name}".'
PROMOTED = 'Your earlier draft was promoted; this is a new draft based on version {version}.'
DISCARDED = 'Your earlier draft was discarded; this is a new draft based on version {version}.'
REPLACED = (
    'Another draft of this document was promoted (version {version}); your earlier draft was removed. '
    'This save started a new draft from the live version.'
)
PERSON_OWNS = 'A person edited your draft of {name}; it is theirs now. Save again after they promote or discard it.'
EDITED = 'A person edited your earlier draft; this is a new draft, and get_document now reads it.'
SAVED = 'Saved draft ({n} lines).'
OUT_OF_RANGE = 'The document has {n} lines.'
DRAFT_NOTE = "This is a draft: a person publishes it on the document's page."
DUPLICATE_TITLE = (
    'A document titled "{title}" is already linked to this {type} ({id}); update it with update_document instead.'
)
CREATED = (
    'Created the draft of a new document "{title}" (id {id}), built for {type} {name}: {n} lines. Change it with '
    "update_document or edit_document and this id; it stays the document's id once published."
)
LINK_LATER = 'It is linked to {type} {name} when a person publishes the draft.'
LINKED_ALREADY = 'It is already linked to {type} {name}.'
MAY_USE = 'Documents you may use: {documents}.'
LINKED_TOO = (
    'Documents linked to a server, service or environment in this run are usable too: list_documents lists them.'
)
RESOURCE_KINDS = ('server', 'service', 'environment')
RESOURCE_TABLES = {
    'server': 'ka.servers',
    'service': 'ka.services',
    'environment': 'ka.environments',
    'code_project': 'ka.repositories',
}
MAX_READ = 50_000
FENCE = re.compile(r'^ {0,3}(```|~~~)')
HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
LIVE = """SELECT version,title,description,purpose,audience,tags,markdown FROM ka.documents
    WHERE id=%s AND project_id=%s AND NOT is_draft"""
# The run's working draft of a live document: its newest draft of it that no person edited.
WORKING = """SELECT id,draft_of,title,description,purpose,audience,tags,markdown FROM ka.documents
    WHERE is_draft AND session_id=%s AND draft_of=%s AND NOT edited ORDER BY created_at DESC,id DESC LIMIT 1"""
# The run's own draft of a new document, by its id (new_document).
OWN_NEW = """SELECT id,draft_of,title,description,purpose,audience,tags,markdown,edited FROM ka.documents
    WHERE id=%s AND is_draft AND draft_of IS NULL AND session_id=%s"""
LINKED = """SELECT server_id,service_id,environment_id,code_project_id FROM ka.document_resources
    WHERE document_id=%s AND removed_at IS NULL"""
# The live document a draft of the run's revises (a mention gives the draft's id).
DRAFT_OF = 'SELECT draft_of FROM ka.documents WHERE id=%s AND is_draft AND session_id=%s AND draft_of IS NOT NULL'
# A document linked to the resource ({column}) with this title (case does not matter): a live one, or a new document
# this run drafted.
SAME_TITLE = """SELECT d.id FROM ka.documents d WHERE d.project_id=%s AND d.draft_of IS NULL
    AND (NOT d.is_draft OR d.session_id=%s) AND lower(d.title)=lower(%s)
    AND EXISTS (SELECT 1 FROM ka.document_resources r WHERE r.document_id=d.id AND r.removed_at IS NULL AND r.{column}=%s)
    ORDER BY d.is_draft,d.id LIMIT 1"""
# The new documents this run drafted that are linked to the resource ({column}).
OWN_NEW_LINKED = """SELECT d.id,d.title,d.description,d.tags FROM ka.documents d
    WHERE d.is_draft AND d.draft_of IS NULL AND d.session_id=%s AND d.project_id=%s
    AND EXISTS (SELECT 1 FROM ka.document_resources r WHERE r.document_id=d.id AND r.removed_at IS NULL AND r.{column}=%s)
    ORDER BY lower(d.title),d.id"""
# The live documents this run has an open draft of.
RUN_DRAFTS = (
    'SELECT draft_of FROM ka.documents WHERE is_draft AND session_id=%s AND draft_of IS NOT NULL AND NOT edited'
)
# search_documents: the expression of the full-text index on live documents (migration 0133), title above description
# above content; a query must use it exactly for the index to serve it.
SEARCH_VECTOR = (
    "(setweight(to_tsvector('english', coalesce(d.title, '')), 'A') "
    "|| setweight(to_tsvector('english', coalesce(d.description, '')), 'B') "
    "|| setweight(to_tsvector('english', coalesce(d.markdown, '')), 'C'))"
)
# The kinds of resource a linked document is readable through (check_get_document, _linked).
LINK_KINDS = tuple(ENTRY_COLUMNS)
# The best live documents the run may read (search_scope: given, or linked to a resource in it) for the query, with a
# snippet of each (ts_headline only on the rows returned).
SEARCH = f"""SELECT found.id,found.title,found.description,found.tags,
    ts_headline('english', found.markdown, websearch_to_tsquery('english', %(query)s),
        'MaxWords=30, MinWords=15, StartSel=**, StopSel=**') AS snippet
    FROM (SELECT d.id,d.title,d.description,d.tags,d.markdown,
        ts_rank_cd({SEARCH_VECTOR}, websearch_to_tsquery('english', %(query)s)) AS rank
        FROM ka.documents d WHERE d.project_id=%(project)s AND NOT d.is_draft
        AND {SEARCH_VECTOR} @@ websearch_to_tsquery('english', %(query)s)
        AND (d.id=ANY(%(document)s::uuid[]) OR EXISTS (SELECT 1 FROM ka.document_resources r
            WHERE r.document_id=d.id AND r.removed_at IS NULL AND (r.server_id=ANY(%(server)s::uuid[])
            OR r.service_id=ANY(%(service)s::uuid[]) OR r.environment_id=ANY(%(environment)s::uuid[])
            OR r.code_project_id=ANY(%(code_project)s::uuid[]))))
        ORDER BY rank DESC,lower(d.title),d.id LIMIT %(limit)s) found
    ORDER BY found.rank DESC,lower(found.title),found.id"""
LINKS = """SELECT document_id,server_id,service_id,environment_id,code_project_id FROM ka.document_resources
    WHERE document_id=ANY(%s) AND removed_at IS NULL"""
NO_MATCH = (
    'No published document matches "{query}". Try other words, or list the documents of a server, service or '
    'environment with list_documents.'
)
MAX_SEARCH = 25


class DraftText(BaseModel):
    title: Title
    description: Description
    markdown: Markdown


def outline(markdown: str) -> list[dict[str, Any]]:
    """The headings (1-based line, level, text), skipping fenced code blocks."""
    headings, fenced = [], False
    for number, line in enumerate(markdown.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        found = None if fenced else HEADING.match(line)
        if found:
            headings.append({'line': number, 'level': len(found.group(1)), 'text': found.group(2)})
    return headings


def _numbered(lines: list[str], first: int) -> str:
    """Lines prefixed with their numbers, cut at MAX_READ characters with a note on where to read on."""
    out, size = [], 0
    for offset, line in enumerate(lines):
        text = f'{first + offset}: {line}'
        if not out and len(text) > MAX_READ:  # one line longer than a read: emit its start, so the read always advances
            return (
                text[:MAX_READ] + f'\n\nLine {first + offset} was cut at 50,000 characters; '
                f'read on with start_line={first + offset + 1}.'
            )
        if size + len(text) + 1 > MAX_READ:
            return '\n'.join(out) + f'\n\nCut at 50,000 characters; read on with start_line={first + offset}.'
        out.append(text)
        size += len(text) + 1
    return '\n'.join(out)


def _outline_text(markdown: str) -> str:
    return '\n'.join(f'{h["line"]}: {"#" * h["level"]} {h["text"]}' for h in outline(markdown)) or '(no headings)'


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _linked(conn, document: UUID | None) -> list[tuple[str, str]]:
    """The resources a document (or a new document's draft) has an active link to (built for or linked), as (type,
    id); none for an id that is not one."""
    if document is None:
        return []
    return [
        (kind, str(row[column]))
        for row in conn.execute(LINKED, (document,)).fetchall()
        for kind, column in ENTRY_COLUMNS.items()
        if row[column] is not None
    ]


def resource_of(access: dict, resource: dict) -> tuple[str, str, str] | str:
    """(type, id, name) of the server, service, environment or code project `resource` names ({type, id}, or {type, name}: case does
    not matter) among the run's resources, else the refusal text."""
    kind = resource['type']
    if not access_rules.has_any(access, kind):
        return access_rules.refuse_none(kind)
    if resource.get('id'):
        found = access_rules.entry(access, kind, resource['id'])
    else:
        wanted = (resource.get('name') or '').strip().lower()
        found = next(
            (item for item in access_rules.entries(access, kind) if (item.get('name') or '').strip().lower() == wanted),
            None,
        )
    if found is None:
        return access_rules.refuse_use(kind)
    return kind, str(found['id']), found.get('name') or ''


def _refuse_document(access: dict) -> str:
    """A document the run may not use: the refusal with the documents it may use (the tool schemas are the same in
    every run, so they do not list them)."""
    parts = [access_rules.refuse_use('document')]
    given = access_rules.listing(access, 'document')
    if given:
        parts.append(
            MAY_USE.format(documents=', '.join(f'{item.get("name") or item["id"]} ({item["id"]})' for item in given))
        )
    if any(access_rules.has_any(access, kind) for kind in RESOURCE_KINDS):
        parts.append(LINKED_TOO)
    return ' '.join(parts)


def check_get_document(access: dict, document_id: str, linked: list[tuple[str, str]] = (), **_) -> str | None:
    """None when allowed, else the refusal text: a document in the run's list, or one linked to a server, service,
    environment or code project in it. `linked`: the document's links, (type, id), loaded by the tool. A document
    linked only to resources outside the list is not usable."""
    if access_rules.can_use(access, 'document', document_id):
        return None
    if any(access_rules.can_use(access, kind, key) for kind, key in linked):
        return None
    return _refuse_document(access)


def readable(conn, access: dict, document: UUID) -> bool:
    """Whether get_document reads this document in a run with this access object."""
    return check_get_document(access, str(document), linked=_linked(conn, document)) is None


def check_list_documents(access: dict, resource: dict, **_) -> str | None:
    """None when allowed, else the refusal text: the resource is in the run's list."""
    found = resource_of(access, resource)
    return found if isinstance(found, str) else None


def check_new_document(access: dict, resource: dict, **_) -> str | None:
    """None when allowed, else the refusal text: the resource the new document is for is in the run's list."""
    return check_list_documents(access, resource)


def check_update_document(
    access: dict, document_id: str, linked: list[tuple[str, str]] = (), resource: dict | None = None, **_
) -> str | None:
    """None when allowed, else the refusal text: a usable document (check_get_document), and the resource to link,
    when given, in the list."""
    if refusal := check_get_document(access, document_id, linked):
        return refusal
    if resource is not None:
        return check_list_documents(access, resource)
    return None


def check_edit_document(access: dict, document_id: str, linked: list[tuple[str, str]] = (), **_) -> str | None:
    """None when allowed, else the refusal text (as check_update_document)."""
    return check_update_document(access, document_id, linked)


def search_scope(access: dict) -> dict[str, list[str]]:
    """check_get_document as SQL parameters (SEARCH): the ids of the documents given to the run and of the servers,
    services, environments and code projects a linked document is readable through. Ids that are not uuids are left
    out: they never name a live document."""
    return {
        kind: [str(key) for item in access_rules.entries(access, kind) if (key := _uuid(item['id'])) is not None]
        for kind in ('document', *LINK_KINDS)
    }


def check_search_documents(access: dict, **_) -> str | None:
    """Always allowed: the search itself keeps to the documents the run may read (search_scope)."""
    return None


def _search_documents(config: dict[str, Any], query: str, limit: int) -> str:
    access = config['access']
    with connect() as conn:
        project = project_of(conn, config)
        rows = conn.execute(SEARCH, {'query': query, 'project': project, 'limit': limit, **search_scope(access)})
        rows = rows.fetchall()
        links = conn.execute(LINKS, ([row['id'] for row in rows],)).fetchall() if rows else []
    if not rows:
        return NO_MATCH.format(query=query)
    resources: dict[UUID, list[dict[str, str]]] = {}
    for link in links:  # only the run's own resources: a run learns nothing about what lies outside its list
        for kind in LINK_KINDS:
            key = link[ENTRY_COLUMNS[kind]]
            found = access_rules.entry(access, kind, str(key)) if key is not None else None
            if found is not None:
                resources.setdefault(link['document_id'], []).append(
                    {'type': kind, 'id': str(key), 'name': found.get('name') or ''}
                )
    documents = [
        {
            'document_id': str(row['id']),
            'title': row['title'],
            'description_short': description_short(row['description']),
            'tags': list(row['tags'] or []),
            'resources': sorted(resources.get(row['id'], []), key=lambda item: (item['type'], item['name'].lower())),
            'snippet': ' '.join(row['snippet'].split()),
        }
        for row in rows
    ]
    return dumps({'documents': documents})


def _document(conn, session, document: UUID | None) -> UUID | None:
    """The document an id names for the run: the live document its own draft revises (a mention gives a draft's
    id), else the id itself."""
    found = conn.execute(DRAFT_OF, (document, session)).fetchone() if document and session else None
    return found['draft_of'] if found else document


def _get_document(
    config: dict[str, Any], document_id: str, section: str | None, start_line: int | None, end_line: int | None
) -> str:
    session = config.get('session_id')
    with connect() as conn:
        document = _document(conn, session, _uuid(document_id))
        key = str(document or document_id)
        if refusal := check_get_document(config['access'], key, linked=_linked(conn, document)):
            return refusal
        project = project_of(conn, config)
        live = conn.execute(LIVE, (document, project)).fetchone()
        if live is None:  # deleted during the run, or a new document's draft: only the run that created it reads it
            own = conn.execute(OWN_NEW, (document, session)).fetchone() if session else None
            if own is None:
                return 'Document not found'
            return _render(str(document), own, None, section, start_line, end_line)
        draft = conn.execute(WORKING, (session, document)).fetchone() if session else None
    return _render(str(document), draft, live, section, start_line, end_line)


def _render(
    document_id: str,
    draft: dict | None,
    live: dict | None,
    section: str | None,
    start_line: int | None,
    end_line: int | None,
) -> str:
    """A read of the run's draft when it has one, else of the live version: a section, a line range or the whole."""
    copy = draft or live
    lines = copy['markdown'].splitlines()
    if section is not None:
        headings = outline(copy['markdown'])
        found = next((h for h in headings if h['text'].strip().lower() == section.strip().lower()), None)
        if found is None:
            return NO_HEADING.format(name=section) + '\n\nOutline:\n' + _outline_text(copy['markdown'])
        end = next(
            (h['line'] - 1 for h in headings if h['line'] > found['line'] and h['level'] <= found['level']), len(lines)
        )
        return _numbered(lines[found['line'] - 1 : end], found['line'])
    if start_line is not None or end_line is not None:
        first, last = start_line or 1, min(end_line or len(lines), len(lines))
        if first > len(lines) or last < first:
            return OUT_OF_RANGE.format(n=len(lines))
        return _numbered(lines[first - 1 : last], first)
    return json.dumps(
        {
            'id': document_id,
            'title': copy['title'],
            'description': copy['description'],
            'reads': 'your draft' if draft else f'the live version {live["version"]}',
            'lines': len(lines),
            'characters': len(copy['markdown']),
            'outline': outline(copy['markdown']),
            'markdown': copy['markdown'][:MAX_MARKDOWN],
            'truncated': len(copy['markdown']) > MAX_MARKDOWN,
        }
    )


def _reply(saved: str, *notes: str) -> str:
    """A write's answer: what was saved, its notes, and that it is a draft."""
    return ' '.join([saved, *(note for note in notes if note), DRAFT_NOTE])


def _save(
    config: dict[str, Any],
    document_id: str,
    change: Callable[[dict[str, Any]], dict[str, Any]],
    resource: dict | None = None,
) -> str:
    """One save; a deadlock with a concurrent document delete, or the document vanishing mid-save, is retried once
    from the start in a new transaction (which finds the run or document gone and says so)."""
    try:
        return _save_once(config, document_id, change, resource)
    except (errors.DeadlockDetected, errors.ForeignKeyViolation):
        return _save_once(config, document_id, change, resource)


def _save_once(
    config: dict[str, Any],
    document_id: str,
    change: Callable[[dict[str, Any]], dict[str, Any]],
    resource: dict | None,
) -> str:
    access = config['access']
    document = _uuid(document_id)
    if document is None:  # not a document id: refused like a document outside the run
        return check_update_document(access, str(document_id), resource=resource) or _refuse_document(access)
    if not config.get('session_id'):
        raise ValueError(NO_SESSION)
    with connect() as conn:
        document = _document(conn, config['session_id'], document)
        if refusal := check_update_document(access, str(document), linked=_linked(conn, document), resource=resource):
            return refusal
        session = _locked_session(conn, config)
        project = session['project_id']
        live = conn.execute(LIVE, (document, project)).fetchone()
        note = ''
        if live is None:  # a new document's draft: only the run that created it saves it
            current = conn.execute(OWN_NEW + ' FOR UPDATE', (document, session['id'])).fetchone()
            if current is None:
                raise ValueError(DOCUMENT_GONE)
            if current['edited']:
                raise ValueError(PERSON_OWNS.format(name=current['title']))
        else:
            current = conn.execute(WORKING + ' FOR UPDATE', (session['id'], document)).fetchone()
            if current is None:
                note = _note(conn, session, document, live['version'])
        text = _text(change, dict(current or live))
        draft = _write(conn, session, current, text, document if live is not None else None)
        linking = _link(conn, access, session, draft, document if live is not None else None, resource)
    return _reply(SAVED.format(n=len(text.markdown.splitlines())), note, linking)


def _link(conn, access: dict, session: dict, draft: UUID, document: UUID | None, resource: dict | None) -> str:
    """Link the saved draft to `resource` (checked): a draft of the live `document` holds the link until it is
    published, a new document's draft has it as the document's own. The note for the answer ('' without resource)."""
    if resource is None:
        return ''
    kind, key, name = resource_of(access, resource)
    words = {'type': access_rules.TYPE_WORDS[kind], 'name': name}
    column = ENTRY_COLUMNS[kind]
    if (
        document is not None
        and conn.execute(
            f'SELECT 1 FROM ka.document_resources WHERE document_id=%s AND removed_at IS NULL AND {column}=%s',
            (document, key),
        ).fetchone()
    ):
        return LINKED_ALREADY.format(**words)
    added = add_link(conn, session['project_id'], draft, kind, UUID(key), Provenance('agent_run', None, session['id']))
    return LINK_LATER.format(**words) if added or document is not None else LINKED_ALREADY.format(**words)


def _text(change: Callable[[dict[str, Any]], dict[str, Any]], base: dict[str, Any]) -> DraftText:
    try:
        return DraftText.model_validate(change(base))
    except ValidationError as exc:
        raise ValueError(str(exc.errors()[0]['msg']).removeprefix('Value error, ')) from None


def _note(conn, session: dict, document: UUID, version: int) -> str:
    """What a save that starts a new draft of the live document `document` (at `version`) says about the run's last
    draft (agent_sessions.last_draft_id) when that was a draft of the same document: promoted, discarded, deleted by
    the promote of another draft (replaced), or still there but edited by a person."""
    outcome = draft_outcome(conn, session['last_draft_id']) if session['last_draft_id'] else None
    if outcome is None or outcome['document_id'] != document:
        return ''
    if outcome['status'] == 'open':
        row = conn.execute('SELECT edited FROM ka.documents WHERE id=%s', (outcome['id'],)).fetchone()
        return EDITED if row is not None and row['edited'] else ''
    notes = {'promoted': PROMOTED, 'discarded': DISCARDED, 'replaced': REPLACED}
    return notes[outcome['status']].format(version=version)


def _write(conn, session: dict, current: dict | None, text: DraftText, document: UUID | None) -> UUID:
    """Save `text` as the run's draft and return its id: update `current`, else create a draft of `document` from its
    live content (title and description from `text`, which started from it). Then recompute the draft's sources from
    the run's tool calls."""
    project = session['project_id']
    prov = Provenance('agent_run', None, session['id'])
    if current is not None:
        update_draft(
            conn,
            current['id'],
            None,
            {**{key: current[key] for key in CONTENT}, **text.model_dump()},
            revision=None,
            edited=False,
        )
        audit(
            conn,
            project,
            None,
            'document.draft.updated',
            'document_draft',
            current['id'],
            {'session_id': session['id'], 'turn': session['turns']},
        )
        draft_id, document = current['id'], current['draft_of']
    else:
        content = {**live_content(conn, project, document), **text.model_dump()}
        draft_id = create_draft(conn, project, prov, content, origin='generated', document_id=document)
    record_run_sources(conn, project, draft_id, document, session['id'], session['turns'])
    return draft_id


def _locked_session(conn, config: dict[str, Any]) -> dict:
    session = conn.execute(
        'SELECT id,project_id,status,turns,last_draft_id FROM ka.agent_sessions WHERE id=%s FOR UPDATE',
        (config['session_id'],),
    ).fetchone()
    if session is None:
        raise ValueError(DOCUMENT_GONE)  # the run went with its document
    if session['status'] == 'closed':
        raise ValueError(CLOSED)
    return session


def _replace(old_text: str, new_text: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        count = base['markdown'].count(old_text)
        if count == 0:
            raise ValueError(NOT_FOUND)
        if count > 1:
            raise ValueError(MANY.format(n=count))
        return {
            'title': base['title'],
            'description': base['description'],
            'markdown': base['markdown'].replace(old_text, new_text, 1),
        }

    return change


def _new_document(
    config: dict[str, Any], resource: dict, title: str, content: str, description: str | None, tags: list[str] | None
) -> str:
    """Create the run's draft of a new document built for the resource (checked): a free slug in the project from the
    resource's name and the title, refused when a document linked to the resource has the title."""
    if not config.get('session_id'):
        raise ValueError(NO_SESSION)
    found = resource_of(config['access'], resource)
    if isinstance(found, str):
        return found
    kind, key, _ = found
    text = _text(lambda _: {'title': title, 'description': description or '', 'markdown': content}, {})
    with connect() as conn:
        session = _locked_session(conn, config)
        project = session['project_id']
        row = conn.execute(f'SELECT id,name FROM {RESOURCE_TABLES[kind]} WHERE id=%s AND project_id=%s', (key, project))
        row = row.fetchone()
        if row is None:  # deleted during the run
            return access_rules.refuse_use(kind)
        clash = conn.execute(
            SAME_TITLE.format(column=ENTRY_COLUMNS[kind]), (project, session['id'], text.title, key)
        ).fetchone()
        if clash is not None:
            return DUPLICATE_TITLE.format(title=text.title, type=access_rules.TYPE_WORDS[kind], id=clash['id'])
        draft = None
        for _ in range(2):  # another run may take the free slug before the insert: look again once
            try:
                with conn.transaction():
                    slug = free_slug(conn, project, document_slug(f'{row["name"]} {text.title}'))
                    draft = create_draft(
                        conn,
                        project,
                        Provenance('agent_run', None, session['id']),
                        {**NEW_DEFAULTS, **text.model_dump(), 'tags': list(tags or [])},
                        origin='generated',
                        slug=slug,
                        links={f'{kind}_ids': [row['id']]},
                    )
                break
            except errors.UniqueViolation:
                continue
            except HTTPException as exc:
                raise ValueError(exc.detail) from None
        if draft is None:
            raise ValueError(NAME_RACE)
        record_run_sources(conn, project, draft, None, session['id'], session['turns'])
    created = CREATED.format(
        title=text.title,
        id=draft,
        type=access_rules.TYPE_WORDS[kind],
        name=row['name'],
        n=len(text.markdown.splitlines()),
    )
    return _reply(created)


def _list_documents(config: dict[str, Any], resource: dict) -> str:
    from knowledge_admin.capabilities.inventory import readable_documents

    found = resource_of(config['access'], resource)
    if isinstance(found, str):
        return found
    kind, key, _ = found
    session = config.get('session_id')
    with connect() as conn:
        project = project_of(conn, config)
        live = readable_documents(conn, config, resource_document_list(conn, project, kind, UUID(key)))
        drafted = {row['draft_of'] for row in conn.execute(RUN_DRAFTS, (session,))} if session else set()
        new = (
            conn.execute(OWN_NEW_LINKED.format(column=ENTRY_COLUMNS[kind]), (session, project, key)).fetchall()
            if session
            else []
        )
    documents = [
        {
            'id': str(item['id']),
            'title': item['title'],
            'description_short': item['description_short'],
            'tags': item['tags'],
            'published': True,
            'your_draft': item['id'] in drafted,
        }
        for item in live
    ] + [
        {
            'id': str(item['id']),
            'title': item['title'],
            'description_short': description_short(item['description']),
            'tags': list(item['tags'] or []),
            'published': False,
            'your_draft': True,
        }
        for item in new
    ]
    cut = len(documents) > MAX_LISTED
    return dumps({'documents': documents[:MAX_LISTED], **({'truncated': True} if cut else {})})


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @field_validator('*')
    @classmethod
    def no_null_characters(cls, value: Any) -> Any:
        texts = value if isinstance(value, list) else [value]
        if any(isinstance(text, str) and '\x00' in text for text in texts):
            raise ValueError('Text cannot contain null characters.')
        return value


class ResourceRef(BaseModel):
    """A server, service, environment or code project of the run, by id or by name."""

    model_config = ConfigDict(extra='forbid')
    type: Literal['server', 'service', 'environment', 'code_project'] = Field(
        description='server, service, environment or code_project.'
    )
    id: str | None = Field(
        None,
        max_length=64,
        description='Its id (the Resource line, get_available_resources, get_resource); or give name instead.',
    )
    name: str | None = Field(None, max_length=200, description='Its name instead of id (case does not matter).')

    @model_validator(mode='after')
    def one(self):
        if (self.id is None) == (self.name is None):
            raise ValueError("Give the resource's id or its name.")
        return self


DocumentId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description="A document id: from search_documents, list_documents, get_resource, new_document or the run's "
        'context, or the id of a draft of this run (a mention).',
    ),
]
Content = Annotated[
    str,
    Field(
        min_length=1, max_length=MAX_MARKDOWN, description='The whole document in Markdown, up to 50,000 characters.'
    ),
]


@tool(usage=texts.GET_DOCUMENT)
async def get_document(
    context,
    document_id: DocumentId,
    section: Annotated[
        str | None,
        Field(max_length=240, description='A heading of the document; returns that section with line numbers.'),
    ] = None,
    start_line: Annotated[
        int | None, Field(ge=1, description='First line to read (1-based); alone, reads to the end.')
    ] = None,
    end_line: Annotated[
        int | None, Field(ge=1, description='Last line to read, inclusive; alone, reads from line 1.')
    ] = None,
) -> str:
    return await asyncio.to_thread(_get_document, context['config'], document_id, section, start_line, end_line)


@tool(usage=texts.SEARCH_DOCUMENTS)
async def search_documents(
    context,
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description='Words to find; "quoted phrase", or, -word to leave out (web search syntax).',
        ),
    ],
    limit: Annotated[int, Field(ge=1, le=MAX_SEARCH, description='How many documents at most, 1–25.')] = 10,
) -> str:
    if refusal := check_search_documents(context['config']['access'], query=query, limit=limit):
        return refusal
    return await asyncio.to_thread(_search_documents, context['config'], query, limit)


@tool(usage=texts.LIST_DOCUMENTS)
async def list_documents(
    context,
    resource: Annotated[
        ResourceRef, Field(description='The server, service, environment or code project, by id or by name.')
    ],
) -> str:
    if refusal := check_list_documents(context['config']['access'], resource=resource):
        return refusal
    return await asyncio.to_thread(_list_documents, context['config'], resource)


@tool(usage=texts.NEW_DOCUMENT)
async def new_document(
    context,
    resource: Annotated[
        ResourceRef,
        Field(description='The server, service, environment or code project the document is for, by id or by name.'),
    ],
    title: Annotated[str, Field(min_length=1, max_length=240, description='The title, 1–240 characters.')],
    content: Content,
    description: Annotated[
        str | None, Field(max_length=4000, description='One or two sentences on what it covers, up to 4000 characters.')
    ] = None,
    tags: Annotated[
        Tags | None, Field(description='Up to 30 short lowercase tags, such as runbook or storage.')
    ] = None,
) -> str:
    if refusal := check_new_document(context['config']['access'], resource=resource):
        return refusal
    return await asyncio.to_thread(_new_document, context['config'], resource, title, content, description, tags)


@tool(usage=texts.UPDATE_DOCUMENT)
async def update_document(
    context,
    document_id: DocumentId,
    content: Content,
    title: Annotated[
        str | None, Field(min_length=1, max_length=240, description='A new title, 1–240 characters; omit to keep it.')
    ] = None,
    description: Annotated[
        str | None, Field(max_length=4000, description='A new description, up to 4000 characters; omit to keep it.')
    ] = None,
    resource: Annotated[
        ResourceRef | None,
        Field(
            description='Also link the document to this server, service, environment or code project when the draft '
            'is published.'
        ),
    ] = None,
) -> str:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        return {
            'title': base['title'] if title is None else title,
            'description': base['description'] if description is None else description,
            'markdown': content,
        }

    return await asyncio.to_thread(_save, context['config'], document_id, change, resource)


class EditDocument(ToolArguments):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)  # config is inherited: no stripping

    document_id: str = Field(
        min_length=1,
        max_length=64,
        description="A document id: from search_documents, list_documents, get_resource, new_document or the run's "
        'context, or the id of a draft of this run (a mention).',
    )
    old_text: str = Field(
        min_length=1, max_length=200_000, description='The exact passage to replace; it must occur exactly once.'
    )
    new_text: str = Field(max_length=1_000_000, description='The text that replaces it; empty removes the passage.')


@tool(arguments=EditDocument, usage=texts.EDIT_DOCUMENT)
async def edit_document(context, document_id: str, old_text: str, new_text: str) -> str:
    return await asyncio.to_thread(_save, context['config'], document_id, _replace(old_text, new_text))
