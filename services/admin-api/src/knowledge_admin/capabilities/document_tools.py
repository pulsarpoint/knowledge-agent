"""The document tools: get_document reads a document the run was given (its own open draft of it when it has one,
else the live version) or one linked to a resource it was given; save_document and edit_document save the run's draft of
a document it was given as editable, or write the new document it was given (an id 'new:<uuid>': the first save creates
the run's new-document draft, later saves update it). Which documents a run may use is in its access object
(config['access']), checked by check_<tool> here through access.py; the run's session is config['session_id'], set by the worker. A save locks the run's
session, then its draft (the order the worker and promote take them in), never ka.projects or ka.documents rows, and
recomputes the draft's sources from the run's tool calls. A draft is a row of ka.documents (is_draft); a draft id is
never a document id here."""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import HTTPException
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.inventory import connect, project_of
from knowledge_admin.documents.api import Description, Markdown, Title
from knowledge_admin.documents.drafts import (
    NAME_IN_USE,
    NEW_DEFAULTS,
    audit,
    available_resources,
    document_named,
    live_content,
    record_run_sources,
)
from knowledge_admin.documents.store import CONTENT, Provenance, create_draft, draft_outcome, update_draft
from knowledge_admin.tools import texts
from knowledge_admin.tools.registry import tool

NO_SESSION = 'The document tools work only in an agent session.'
MAX_MARKDOWN = 50_000
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
WRITE_FIRST = 'Write the document with save_document first.'
NEW_PREFIX = 'new:'
MAY_CHANGE = 'Documents you may change: {documents}.'
MAX_READ = 50_000
FENCE = re.compile(r'^ {0,3}(```|~~~)')
HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
LIVE = """SELECT version,title,description,purpose,audience,tags,markdown FROM ka.documents
    WHERE id=%s AND project_id=%s AND NOT is_draft"""
# The run's working draft of a live document: its newest draft of it that no person edited.
WORKING = """SELECT id,draft_of,title,description,purpose,audience,tags,markdown FROM ka.documents
    WHERE is_draft AND session_id=%s AND draft_of=%s AND NOT edited ORDER BY created_at DESC,id DESC LIMIT 1"""
LINKED = """SELECT server_id,service_id,environment_id,code_project_id FROM ka.document_resources
    WHERE document_id=%s AND removed_at IS NULL"""
# The run's working draft of its new document: the brand-new draft with the entry's folder and slug, or its draft of
# the live document that folder and slug now name.
NEW_WORKING = """SELECT id,draft_of,title,description,purpose,audience,tags,markdown FROM ka.documents
    WHERE is_draft AND session_id=%s AND NOT edited AND ((draft_of IS NULL AND folder IS NOT DISTINCT FROM %s AND slug=%s)
    OR draft_of=(SELECT id FROM ka.documents WHERE project_id=%s AND NOT is_draft AND folder IS NOT DISTINCT FROM %s
    AND slug=%s)) ORDER BY created_at DESC,id DESC LIMIT 1"""


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


def _document_id(value: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise ValueError(access_rules.refuse_use('document')) from None


def _linked(conn, document: UUID) -> list[tuple[str, str]]:
    """The resources a document has an active link to (built for or linked), as (type, id)."""
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


def check_get_document(access: dict, document_id: str, linked: list[tuple[str, str]] = (), **_) -> str | None:
    """None when allowed, else the refusal text: a document in the run's list, or one linked to a server, service,
    environment or code project in it (read-only; get_resource lists them). `linked`: the document's links, (type, id),
    loaded by the tool. A document linked only to resources outside the list is not readable."""
    if access_rules.can_use(access, 'document', document_id):
        return None
    if any(access_rules.can_use(access, kind, key) for kind, key in linked):
        return None
    return access_rules.refuse_use('document')


def readable(conn, access: dict, document: UUID) -> bool:
    """Whether get_document reads this document in a run with this access object."""
    return check_get_document(access, str(document), linked=_linked(conn, document)) is None


def check_save_document(access: dict, document_id: str, **_) -> str | None:
    """None when allowed, else the refusal text: a content editor, a document in the list that is editable, and for
    the new document every resource it is built for in the list. A refused id is answered with the documents the run
    may change (the tool schemas are the same in every run, so they do not list them)."""
    if not access_rules.is_content_editor(access):
        return access_rules.NOT_EDITOR
    if not access_rules.has_any(access, 'document'):
        return access_rules.refuse_none('document')
    if not access_rules.can_write_document(access, document_id):
        refusal = access_rules.refuse_use('document')
        editable = [entry for entry in access_rules.listing(access, 'document') if entry.get('editable')]
        if not editable:
            return refusal
        names = ', '.join(f'{entry["name"]} ({entry["id"]})' for entry in editable)
        return f'{refusal} {MAY_CHANGE.format(documents=names)}'
    entry = access_rules.entry(access, 'document', document_id)
    built_for = [(kind, key) for kind, key in entry.get('built_for') or []]
    if entry.get('new') and not access_rules.can_create_document(access, built_for):
        kind = next(kind for kind, key in built_for if not access_rules.can_use(access, kind, key))
        return access_rules.refuse_use(kind)
    return None


def check_edit_document(access: dict, document_id: str, **_) -> str | None:
    """None when allowed, else the refusal text (as check_save_document)."""
    return check_save_document(access, document_id)


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
    try:
        document = _document_id(document_id)
    except ValueError as exc:
        return str(exc)
    with connect() as conn:
        project = project_of(conn, config)
        live = conn.execute(LIVE, (document, project)).fetchone()
        if refusal := check_get_document(config['access'], str(document), linked=_linked(conn, document)):
            return refusal
        if live is None:
            return 'Document not found'  # deleted during the run (or a draft id: never read as a document)
        session = config.get('session_id')
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


def _save(config: dict[str, Any], document_id: str, change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    """One save; a deadlock with a concurrent document delete, or the document vanishing mid-save, is retried once
    from the start in a new transaction (which finds the run or document gone and says so)."""
    try:
        return _save_once(config, document_id, change)
    except (errors.DeadlockDetected, errors.ForeignKeyViolation):
        return _save_once(config, document_id, change)


def _save_once(config: dict[str, Any], document_id: str, change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    entry = access_rules.entry(config['access'], 'document', document_id)  # the tool checked it
    if str(document_id).startswith(NEW_PREFIX):
        return _save_new(config, entry, change)
    document = _document_id(document_id)
    if not config.get('session_id'):
        raise ValueError(NO_SESSION)
    with connect() as conn:
        session = _locked_session(conn, config)
        project = session['project_id']
        live = conn.execute(LIVE, (document, project)).fetchone()
        if live is None:
            raise ValueError(DOCUMENT_GONE)
        current = conn.execute(WORKING + ' FOR UPDATE', (session['id'], document)).fetchone()
        text = _text(change, dict(current or live))
        note = ''
        if current is None:
            note = _note(conn, session, document, live['version'])
        _write(conn, config, session, current, text, document)
    saved = SAVED.format(n=len(text.markdown.splitlines()))
    return f'{saved} {note}' if note else saved


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
    """What the new document links on promote (its access entry's links: what the person chose, an environment, not
    what it brings for the tools), as resource_ids."""
    links = {key: [] for key in ('code_project_ids', 'environment_ids', 'service_ids', 'server_ids', 'deployment_ids')}
    for kind, key in new.get('links') or []:
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
) -> None:
    """Save `text` as the run's draft: update `current`, else create a draft of `document` from its live content (title
    and description from `text`, which started from it), else (no document) of the new document `new`
    ({folder, slug, tags}; tagged inventory: the inventory document of the run's one chosen resource). Then recompute
    the draft's sources from the run's tool calls."""
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


def _save_new(config: dict[str, Any], entry: dict[str, Any], change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
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
        _write(conn, config, session, current, text, existing['id'] if existing is not None else None, entry)
    saved = SAVED.format(n=len(text.markdown.splitlines()))
    return f'{saved} {note}' if note else saved


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


@tool(usage=texts.GET_DOCUMENT)
async def get_document(
    context,
    document_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            description='A document id: one given to this run, or one listed by get_resource.',
        ),
    ],
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


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @field_validator('*')
    @classmethod
    def no_null_characters(cls, value: Any) -> Any:
        texts = value if isinstance(value, list) else [value]
        if any(isinstance(text, str) and '\x00' in text for text in texts):
            raise ValueError('Text cannot contain null characters.')
        return value


class SaveDocument(Proposal):
    model_config = ConfigDict(
        extra='forbid', str_strip_whitespace=False
    )  # config is inherited: switch stripping off explicitly

    document_id: str = Field(min_length=1, max_length=64, description='The id of a document given to this run to edit.')
    markdown: str = Field(min_length=1, max_length=1_000_000, description='The complete document in Markdown.')
    title: str | None = Field(None, min_length=1, max_length=240, description='A new title; omit it to keep the title.')
    description: str | None = Field(None, max_length=4000, description='A new description; omit it to keep it.')


class EditDocument(Proposal):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)

    document_id: str = Field(min_length=1, max_length=64, description='The id of a document given to this run to edit.')
    old_text: str = Field(
        min_length=1, max_length=200_000, description='The exact passage to replace; it must occur exactly once.'
    )
    new_text: str = Field(max_length=1_000_000, description='The text that replaces it; empty removes the passage.')


@tool(arguments=SaveDocument)
async def save_document(
    context, document_id: str, markdown: str, title: str | None = None, description: str | None = None
) -> str:
    """Save your draft of the document with this id: the complete text (optionally a new title or description). A person reviews the draft and decides whether it becomes the live version."""
    if refusal := check_save_document(context['config']['access'], document_id=document_id):
        return refusal

    def change(base: dict[str, Any]) -> dict[str, Any]:
        if base['markdown'] is None:  # the first save of a new document (or of the existing one its name matched)
            return {
                'title': title or base['title'],
                'description': base['description'] if description is None else description,
                'markdown': markdown,
            }
        return {
            'title': base['title'] if title is None else title,
            'description': base['description'] if description is None else description,
            'markdown': markdown,
        }

    return await asyncio.to_thread(_save, context['config'], document_id, change)


@tool(arguments=EditDocument)
async def edit_document(context, document_id: str, old_text: str, new_text: str) -> str:
    """Replace one exact passage of the document with this id (old_text, which must occur exactly once) with new_text, and save the result as your draft. An empty new_text removes the passage."""
    if refusal := check_edit_document(context['config']['access'], document_id=document_id):
        return refusal
    return await asyncio.to_thread(_save, context['config'], document_id, _replace(old_text, new_text))
