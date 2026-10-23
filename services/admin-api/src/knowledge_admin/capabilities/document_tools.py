"""The document tools. Every agent write to a document is a draft in the real documents, which a person publishes on
the document's page: list_documents lists a resource's documents with the run's drafts; get_document reads a document
(the run's open draft of it when it has one, else the live version); new_document creates a new document as a draft
built for a server, service or environment; update_document (the whole text, optionally linking a resource) and
edit_document (one exact passage) save the run's draft of a document.

A document is usable (read and drafted) when it is in the run's access object (config['access']) or linked to a
resource in it; a new document needs its resource in the access object. Checked by check_<tool> here, pure functions
over the access object (access.py); there is no role. The run's session is config['session_id'], set by the worker.

Document ids: a live document's id; a new document's id is the id of its draft (new_document returns it), which only
the run that created it uses until a person publishes the draft, and which stays the document's id once published
(store.promote_draft keeps the row). A run started from Documents → New writes the new document it was given, an
id 'new:<uuid>' in its access object: the first save creates the run's new-document draft, later saves update it.

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
from knowledge_admin.documents.drafts import (
    NAME_IN_USE,
    NEW_DEFAULTS,
    audit,
    available_resources,
    document_named,
    free_folder,
    free_slug,
    live_content,
    record_run_sources,
)
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
NOT_WRITTEN = 'Not written yet.'
WRITE_FIRST = 'Write the document with update_document first.'
NEW_PREFIX = 'new:'
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
RESOURCE_TABLES = {'server': 'ka.servers', 'service': 'ka.services', 'environment': 'ka.environments'}
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
# The run's working draft of its new document: the brand-new draft with the entry's folder and slug, or its draft of
# the live document that folder and slug now name.
NEW_WORKING = """SELECT id,draft_of,title,description,purpose,audience,tags,markdown FROM ka.documents
    WHERE is_draft AND session_id=%s AND NOT edited AND ((draft_of IS NULL AND folder IS NOT DISTINCT FROM %s AND slug=%s)
    OR draft_of=(SELECT id FROM ka.documents WHERE project_id=%s AND NOT is_draft AND folder IS NOT DISTINCT FROM %s
    AND slug=%s)) ORDER BY created_at DESC,id DESC LIMIT 1"""
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
        for kind, column in (
            ('server', 'server_id'),
            ('service', 'service_id'),
            ('environment', 'environment_id'),
            ('code_project', 'code_project_id'),
        )
        if row[column] is not None
    ]


def resource_of(access: dict, resource: dict) -> tuple[str, str, str] | str:
    """(type, id, name) of the server, service or environment `resource` names ({type, id}, or {type, name}: case does
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
    """None when allowed, else the refusal text: a usable document (check_get_document; the new document a run was
    given needs every resource it is built for in the list too), and the resource to link, when given, in the list."""
    if refusal := check_get_document(access, document_id, linked):
        return refusal
    entry = access_rules.entry(access, 'document', document_id)
    if entry is not None and entry.get('new'):
        built_for = [(kind, key) for kind, key in entry.get('built_for') or []]
        if not access_rules.can_create_document(access, built_for):
            kind = next(kind for kind, key in built_for if not access_rules.can_use(access, kind, key))
            return access_rules.refuse_use(kind)
    if resource is not None:
        return check_list_documents(access, resource)
    return None


def check_edit_document(access: dict, document_id: str, linked: list[tuple[str, str]] = (), **_) -> str | None:
    """None when allowed, else the refusal text (as check_update_document)."""
    return check_update_document(access, document_id, linked)


def _new_document_draft(conn, session_id, project, entry: dict[str, Any], lock: bool = False) -> dict | None:
    """The run's open, unedited draft of its new document: the new-document draft with the entry's folder and slug, or
    (when that folder and slug named an existing document at the first save) its draft of that document."""
    folder, slug = entry['folder'], entry['slug']
    return conn.execute(
        NEW_WORKING + (' FOR UPDATE' if lock else ''), (session_id, folder, slug, project, folder, slug)
    ).fetchone()


def _get_new_document(config: dict[str, Any], document_id: str) -> tuple[dict | None, str | None]:
    """(the run's draft of its new document, or None with the answer to give instead); the caller checked it."""
    entry = access_rules.entry(config['access'], 'document', document_id)
    session = config.get('session_id')
    if not session:
        return None, NOT_WRITTEN
    with connect() as conn:
        draft = _new_document_draft(conn, session, project_of(conn, config), entry)
    return (draft, None) if draft is not None else (None, NOT_WRITTEN)


def _get_document(
    config: dict[str, Any], document_id: str, section: str | None, start_line: int | None, end_line: int | None
) -> str:
    if str(document_id).startswith(NEW_PREFIX):
        if refusal := check_get_document(config['access'], document_id):
            return refusal
        draft, answer = _get_new_document(config, document_id)
        if draft is None:
            return answer
        return _render(document_id, draft, None, section, start_line, end_line)
    document = _uuid(document_id)
    with connect() as conn:
        if refusal := check_get_document(config['access'], str(document_id), linked=_linked(conn, document)):
            return refusal
        project = project_of(conn, config)
        live = conn.execute(LIVE, (document, project)).fetchone()
        session = config.get('session_id')
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
    if str(document_id).startswith(NEW_PREFIX):
        if refusal := check_update_document(access, document_id, resource=resource):
            return refusal
        return _save_new(config, access_rules.entry(access, 'document', document_id), change, resource)
    document = _uuid(document_id)
    if document is None:  # not a document id: refused like a document outside the run
        return check_update_document(access, str(document_id), resource=resource) or _refuse_document(access)
    if not config.get('session_id'):
        raise ValueError(NO_SESSION)
    with connect() as conn:
        if refusal := check_update_document(
            access, str(document_id), linked=_linked(conn, document), resource=resource
        ):
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
        draft = _write(conn, config, session, current, text, document if live is not None else None)
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


def _run_links(new: dict[str, Any]) -> dict[str, list[str]]:
    """What the new document is built for (its access entry's built_for: what the person chose, an environment, not
    what it brings for the tools), as the resource_ids it links on promote."""
    links = {key: [] for key in ('code_project_ids', 'environment_ids', 'service_ids', 'server_ids', 'deployment_ids')}
    for kind, key in new.get('built_for') or []:
        links[f'{kind}_ids'].append(str(key))
    return links


def _write(
    conn,
    config: dict[str, Any],
    session: dict,
    current: dict | None,
    text: DraftText,
    document: UUID | None,
    new: dict[str, Any] | None = None,
) -> UUID:
    """Save `text` as the run's draft and return its id: update `current`, else create a draft of `document` from its
    live content (title and description from `text`, which started from it), else (no document) of the new document
    `new` ({folder, slug, tags}; tagged inventory: the inventory document of the run's one chosen resource). Then
    recompute the draft's sources from the run's tool calls."""
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
    elif document is not None:
        content = {**live_content(conn, project, document), **text.model_dump()}
        draft_id = create_draft(conn, project, prov, content, origin='generated', document_id=document)
    else:
        try:
            draft_id = create_draft(
                conn,
                project,
                prov,
                {**NEW_DEFAULTS, **text.model_dump(), 'tags': list(new.get('tags') or [])},
                origin='generated',
                slug=new['slug'],
                folder=new['folder'],
                links=available_resources(conn, project, _run_links(new)),
            )
        except errors.UniqueViolation:  # another run's or a person's brand-new draft holds this folder and slug
            raise ValueError(NAME_IN_USE.format(slug=new['slug'])) from None
        except HTTPException as exc:  # a live document took the name of a new document tagged inventory meanwhile
            raise ValueError(exc.detail) from None
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


def _save_new(
    config: dict[str, Any],
    entry: dict[str, Any],
    change: Callable[[dict[str, Any]], dict[str, Any]],
    resource: dict | None,
) -> str:
    """A save of the run's new document (the tool checked it): the first creates its draft, later ones update
    that draft. When the entry's folder and slug name an existing document (also one a person promoted the run's
    earlier draft into), the first save creates a draft of that document from its live content instead."""
    if not config.get('session_id'):
        raise ValueError(NO_SESSION)
    with connect() as conn:
        session = _locked_session(conn, config)
        project = session['project_id']
        current = _new_document_draft(conn, session['id'], project, entry, lock=True)
        existing = None if current is not None else document_named(conn, project, entry['folder'], entry['slug'])
        if current is None and existing is None:
            # The run's brand-new draft, edited by a person, holds the name: no second draft until they decide.
            owned = conn.execute(
                """SELECT title FROM ka.documents WHERE is_draft AND session_id=%s AND draft_of IS NULL AND edited
                AND folder IS NOT DISTINCT FROM %s AND slug=%s""",
                (session['id'], entry['folder'], entry['slug']),
            ).fetchone()
            if owned is not None:
                raise ValueError(PERSON_OWNS.format(name=owned['title']))
        live = live_content(conn, project, existing['id']) if existing is not None else None
        # markdown None marks the first save: edit_document has nothing to change yet
        base = (
            dict(current)
            if current is not None
            else {
                'title': live['title'] if live else entry['name'],
                'description': live['description'] if live else '',
                'markdown': None,
            }
        )
        text = _text(change, base)
        note = ''
        if current is None and existing is not None:
            note = _note(conn, session, existing['id'], existing['version'])
        document = existing['id'] if existing is not None else current['draft_of'] if current is not None else None
        draft = _write(conn, config, session, current, text, existing['id'] if existing is not None else None, entry)
        linking = _link(conn, config['access'], session, draft, document, resource)
    return _reply(SAVED.format(n=len(text.markdown.splitlines())), note, linking)


def _replace(old_text: str, new_text: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        if base['markdown'] is None:
            raise ValueError(WRITE_FIRST)
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
    """Create the run's draft of a new document built for the resource (checked): folder and slug from the resource
    and the title (as discovery named them), refused when a document linked to the resource has the title."""
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
        # The export lock a promote takes, so the free name below cannot race a Publish of another draft.
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'{project}:document-export',))
        try:
            folder = free_folder(conn, project, kind, document_slug(row['name']) or kind)
            slug = free_slug(conn, project, folder, document_slug(f'{row["name"]} {text.title}'))
            draft = create_draft(
                conn,
                project,
                Provenance('agent_run', None, session['id']),
                {**NEW_DEFAULTS, **text.model_dump(), 'tags': list(tags or [])},
                origin='generated',
                slug=slug,
                folder=folder,
                links={f'{kind}_ids': [row['id']]},
            )
        except errors.UniqueViolation:  # another draft took the name meanwhile
            raise ValueError(NAME_IN_USE.format(slug=slug)) from None
        except HTTPException as exc:
            raise ValueError(exc.detail) from None
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
    """A server, service or environment of the run, by id or by name."""

    model_config = ConfigDict(extra='forbid')
    type: Literal['server', 'service', 'environment'] = Field(description='server, service or environment.')
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
        description="A document id: from list_documents, get_resource, new_document or the run's context.",
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


@tool(usage=texts.LIST_DOCUMENTS)
async def list_documents(
    context,
    resource: Annotated[ResourceRef, Field(description='The server, service or environment, by id or by name.')],
) -> str:
    if refusal := check_list_documents(context['config']['access'], resource=resource):
        return refusal
    return await asyncio.to_thread(_list_documents, context['config'], resource)


@tool(usage=texts.NEW_DOCUMENT)
async def new_document(
    context,
    resource: Annotated[
        ResourceRef, Field(description='The server, service or environment the document is for, by id or by name.')
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
        Field(description='Also link the document to this server, service or environment when the draft is published.'),
    ] = None,
) -> str:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        first = base['markdown'] is None  # the first save of a new document (or of the existing one its name matched)
        return {
            'title': (title or base['title']) if first else (base['title'] if title is None else title),
            'description': base['description'] if description is None else description,
            'markdown': content,
        }

    return await asyncio.to_thread(_save, context['config'], document_id, change, resource)


class EditDocument(ToolArguments):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)  # config is inherited: no stripping

    document_id: str = Field(
        min_length=1,
        max_length=64,
        description="A document id: from list_documents, get_resource, new_document or the run's context.",
    )
    old_text: str = Field(
        min_length=1, max_length=200_000, description='The exact passage to replace; it must occur exactly once.'
    )
    new_text: str = Field(max_length=1_000_000, description='The text that replaces it; empty removes the passage.')


@tool(arguments=EditDocument, usage=texts.EDIT_DOCUMENT)
async def edit_document(context, document_id: str, old_text: str, new_text: str) -> str:
    return await asyncio.to_thread(_save, context['config'], document_id, _replace(old_text, new_text))
