"""The document tools: get_document reads a document the run was given (its own open draft of it when it has one,
else the live version) or one attached to an inventory entry; save_document and edit_document save the run's draft of
a document it was given as editable. Which documents a run may use is pinned in its config (document_access) and
checked here with allowed(); the run's session is config['session_id'], set by the worker. A save locks the run's
session, then its draft (the order the worker and promote take them in), never ka.projects or ka.documents rows, and
recomputes the draft's sources from the run's tool calls."""
import asyncio
import json
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg import errors
from pydantic import BaseModel, ValidationError

from knowledge_admin.capabilities.context import NOT_AVAILABLE, allowed
from knowledge_admin.capabilities.inventory import connect, project_of
from knowledge_admin.document_drafts import audit, create_draft, record_run_sources
from knowledge_admin.documents import Description, Markdown, Title

NO_SESSION = 'The document tools work only in an agent session.'
MAX_MARKDOWN = 50_000
DOCUMENT_GONE = 'This document no longer exists.'
CLOSED = 'This run is closed.'
NOT_FOUND = 'This text is not in the document. Read it again with get_document.'
MANY = 'This text appears {n} times. Include more of the surrounding text.'
NO_HEADING = 'No heading named "{name}".'
PROMOTED = 'Your earlier draft was promoted; this is a new draft based on version {version}.'
DISCARDED = 'Your earlier draft was discarded; this is a new draft based on version {version}.'
EDITED = 'A person edited your earlier draft; this is a new draft, and get_document now reads it.'
SAVED = 'Saved draft ({n} lines).'
OUT_OF_RANGE = 'The document has {n} lines.'
MAX_READ = 50_000
FENCE = re.compile(r'^ {0,3}(```|~~~)')
HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
LIVE = '''SELECT v.id AS version_id,v.version,v.title,v.description,v.purpose,v.audience,v.tags,v.markdown
    FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s AND d.project_id=%s'''
LIVE = '''SELECT v.id AS version_id,v.version,v.title,v.description,v.purpose,v.audience,v.tags,v.markdown
    FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s AND d.project_id=%s'''
WORKING = '''SELECT id,title,description,purpose,audience,tags,markdown FROM ka.document_drafts
    WHERE session_id=%s AND document_id=%s AND status='open' AND NOT edited ORDER BY created_at DESC,id DESC LIMIT 1'''
ATTACHED = 'SELECT 1 FROM ka.inventory_documents WHERE document_id=%s'


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
            return (text[:MAX_READ] + f'\n\nLine {first + offset} was cut at 50,000 characters; '
                    f'read on with start_line={first + offset + 1}.')
        if size + len(text) + 1 > MAX_READ:
            return '\n'.join(out) + f'\n\nCut at 50,000 characters; read on with start_line={first + offset}.'
        out.append(text)
        size += len(text) + 1
    return '\n'.join(out)


def _outline_text(markdown: str) -> str:
    return '\n'.join(f"{h['line']}: {'#' * h['level']} {h['text']}" for h in outline(markdown)) or '(no headings)'


def _document_id(value: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise ValueError(NOT_AVAILABLE.format(kind='document')) from None


def _readable(conn, config: dict[str, Any], document: UUID) -> bool:
    """A document the run was given, or one attached to an inventory entry (read-only, as get_document always allowed)."""
    try:
        allowed(config, 'document', str(document))
        return True
    except ValueError:
        return conn.execute(ATTACHED, (document,)).fetchone() is not None


def _get_document(config: dict[str, Any], document_id: str, section: str | None, start_line: int | None,
                  end_line: int | None) -> str:
    try:
        document = _document_id(document_id)
    except ValueError as exc:
        return str(exc)
    with connect() as conn:
        project = project_of(conn, config)
        live = conn.execute(LIVE, (document, project)).fetchone()
        if live is None or not _readable(conn, config, document):
            return NOT_AVAILABLE.format(kind='document')
        session = config.get('session_id')
        draft = conn.execute(WORKING, (session, document)).fetchone() if session else None
    copy = draft or live
    lines = copy['markdown'].splitlines()
    if section is not None:
        headings = outline(copy['markdown'])
        found = next((h for h in headings if h['text'].strip().lower() == section.strip().lower()), None)
        if found is None:
            return NO_HEADING.format(name=section) + '\n\nOutline:\n' + _outline_text(copy['markdown'])
        end = next((h['line'] - 1 for h in headings if h['line'] > found['line'] and h['level'] <= found['level']), len(lines))
        return _numbered(lines[found['line'] - 1:end], found['line'])
    if start_line is not None or end_line is not None:
        first, last = start_line or 1, min(end_line or len(lines), len(lines))
        if first > len(lines) or last < first:
            return OUT_OF_RANGE.format(n=len(lines))
        return _numbered(lines[first - 1:last], first)
    return json.dumps({'id': str(document), 'title': copy['title'], 'description': copy['description'],
                       'reads': 'your draft' if draft else f"the live version {live['version']}",
                       'lines': len(lines), 'characters': len(copy['markdown']), 'outline': outline(copy['markdown']),
                       'markdown': copy['markdown'][:MAX_MARKDOWN], 'truncated': len(copy['markdown']) > MAX_MARKDOWN})


def _save(config: dict[str, Any], document_id: str, change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    """One save; a deadlock with a concurrent document delete, or the document vanishing mid-save, is retried once
    from the start in a new transaction (which finds the run or document gone and says so)."""
    try:
        return _save_once(config, document_id, change)
    except (errors.DeadlockDetected, errors.ForeignKeyViolation):
        return _save_once(config, document_id, change)


def _save_once(config: dict[str, Any], document_id: str, change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    allowed(config, 'document', document_id, write=True)
    document = _document_id(document_id)
    if not config.get('session_id'):
        raise ValueError(NO_SESSION)
    with connect() as conn:
        session = conn.execute('SELECT id,project_id,status,turns FROM ka.agent_sessions WHERE id=%s FOR UPDATE',
                               (config['session_id'],)).fetchone()
        if session is None:
            raise ValueError(DOCUMENT_GONE)  # the run went with its document
        if session['status'] == 'closed':
            raise ValueError(CLOSED)
        project = session['project_id']
        live = conn.execute(LIVE, (document, project)).fetchone()
        if live is None:
            raise ValueError(DOCUMENT_GONE)
        current = conn.execute(WORKING + ' FOR UPDATE', (session['id'], document)).fetchone()
        base = dict(current or live)
        try:
            text = DraftText.model_validate(change(base))
        except ValidationError as exc:
            raise ValueError(str(exc.errors()[0]['msg']).removeprefix('Value error, ')) from None
        note = ''
        if current is not None:
            conn.execute('UPDATE ka.document_drafts SET title=%s,description=%s,markdown=%s,revision=revision+1 WHERE id=%s',
                         (text.title, text.description, text.markdown, current['id']))
            audit(conn, project, None, 'document.draft.updated', 'document_draft', current['id'],
                  {'session_id': session['id'], 'turn': session['turns']})
            draft_id = current['id']
        else:
            previous = conn.execute('''SELECT status,edited FROM ka.document_drafts WHERE session_id=%s AND document_id=%s
                ORDER BY created_at DESC,id DESC LIMIT 1''', (session['id'], document)).fetchone()
            if previous is not None:
                note = (EDITED if previous['status'] == 'open'
                        else (PROMOTED if previous['status'] == 'promoted' else DISCARDED).format(version=live['version']))
            content = {key: live[key] for key in ('purpose', 'audience', 'tags')} | text.model_dump()
            draft_id = create_draft(conn, project, None, content, origin='agent', document_id=document,
                                    session_id=session['id'])
        base_version = conn.execute('SELECT base_version_id FROM ka.document_drafts WHERE id=%s', (draft_id,)).fetchone()
        record_run_sources(conn, project, draft_id, document, base_version['base_version_id'], session['id'],
                           session['turns'])
    saved = SAVED.format(n=len(text.markdown.splitlines()))
    return f'{saved} {note}' if note else saved


def _replace(old_text: str, new_text: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        count = base['markdown'].count(old_text)
        if count == 0:
            raise ValueError(NOT_FOUND)
        if count > 1:
            raise ValueError(MANY.format(n=count))
        return {'title': base['title'], 'description': base['description'],
                'markdown': base['markdown'].replace(old_text, new_text, 1)}
    return change


async def get_document(context, document_id: str, section: str | None = None, start_line: int | None = None,
                       end_line: int | None = None) -> str:
    return await asyncio.to_thread(_get_document, context['config'], document_id, section, start_line, end_line)


async def save_document(context, document_id: str, markdown: str, title: str | None = None,
                        description: str | None = None) -> str:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        return {'title': base['title'] if title is None else title,
                'description': base['description'] if description is None else description, 'markdown': markdown}
    return await asyncio.to_thread(_save, context['config'], document_id, change)


async def edit_document(context, document_id: str, old_text: str, new_text: str) -> str:
    return await asyncio.to_thread(_save, context['config'], document_id, _replace(old_text, new_text))
