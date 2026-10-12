"""Ask agent runs (purpose revision): the document tools. get_doc reads the run's working copy — its latest open draft
no person has edited, else the document's live version; save_doc and edit_doc save the run's draft. A save locks the
run's session, then its draft (the order the worker and promote take them in), never ka.projects or ka.documents rows,
and recomputes the draft's sources from the run's tool calls."""
import asyncio
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from knowledge_admin.capabilities.inventory import connect
from knowledge_admin.document_drafts import audit, create_draft, record_run_sources
from knowledge_admin.documents import Description, Markdown, Title

NOT_REVISION = 'The document tools work only in an Ask agent run.'
DOCUMENT_GONE = 'This document no longer exists.'
CLOSED = 'This run is closed.'
NOT_FOUND = 'This text is not in the document. Read it again with get_doc.'
MANY = 'This text appears {n} times. Include more of the surrounding text.'
NO_HEADING = 'No heading named "{name}".'
PROMOTED = 'Your earlier draft was promoted; this is a new draft based on version {version}.'
DISCARDED = 'Your earlier draft was discarded; this is a new draft based on version {version}.'
EDITED = 'A person edited your earlier draft; this is a new draft, and get_doc now reads it.'
SAVED = 'Saved draft ({n} lines).'
MAX_READ = 50_000
FENCE = re.compile(r'^ {0,3}(```|~~~)')
HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
LIVE = '''SELECT v.id AS version_id,v.version,v.title,v.description,v.purpose,v.audience,v.tags,v.markdown
    FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s AND d.project_id=%s'''
WORKING = '''SELECT id,title,description,purpose,audience,tags,markdown FROM ka.document_drafts
    WHERE session_id=%s AND status='open' AND NOT edited ORDER BY created_at DESC,id DESC LIMIT 1'''


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
        if size + len(text) + 1 > MAX_READ:
            return '\n'.join(out) + f'\n\nCut at 50,000 characters; read on with start_line={first + offset}.'
        out.append(text)
        size += len(text) + 1
    return '\n'.join(out)


def _outline_text(markdown: str) -> str:
    return '\n'.join(f"{h['line']}: {'#' * h['level']} {h['text']}" for h in outline(markdown)) or '(no headings)'


def _run(conn, config: dict[str, Any], *, lock: bool) -> tuple[dict, dict]:
    """The run's session (locked for a save) and its document's live version; ValueError when either is gone."""
    run = config.get('revision')
    if not run:
        raise ValueError(NOT_REVISION)
    session = conn.execute("SELECT id,project_id,status,turns,target_document_id FROM ka.agent_sessions WHERE id=%s"
                           " AND purpose='revision'" + (' FOR UPDATE' if lock else ''), (run['session_id'],)).fetchone()
    if session is None:
        raise ValueError(DOCUMENT_GONE)  # the run went with its document
    live = conn.execute(LIVE, (session['target_document_id'], session['project_id'])).fetchone()
    if live is None:
        raise ValueError(DOCUMENT_GONE)
    return session, live


def _get_doc(config: dict[str, Any], section: str | None, start_line: int | None, end_line: int | None) -> str:
    with connect() as conn:
        session, live = _run(conn, config, lock=False)
        draft = conn.execute(WORKING, (session['id'],)).fetchone()
    copy = draft or live
    lines = copy['markdown'].splitlines()
    if section is not None:
        headings = outline(copy['markdown'])
        found = next((h for h in headings if h['text'].strip().lower() == section.strip().lower()), None)
        if found is None:
            return NO_HEADING.format(name=section) + '\n\nOutline:\n' + _outline_text(copy['markdown'])
        end = next((h['line'] - 1 for h in headings if h['line'] > found['line'] and h['level'] <= found['level']), len(lines))
        return _numbered(lines[found['line'] - 1:end], found['line'])
    if start_line is not None:
        last = min(end_line or len(lines), len(lines))
        return _numbered(lines[start_line - 1:last], start_line)
    reads = 'your draft' if draft else f"the live version {live['version']}"
    return '\n'.join([f"Title: {copy['title']}", f"Description: {copy['description'] or '(none)'}", f'Reads: {reads}',
                      f"Length: {len(lines)} lines, {len(copy['markdown'])} characters", '', 'Outline:',
                      _outline_text(copy['markdown']), '',
                      'Read the text with section (a heading) or start_line/end_line.'])


def _save(config: dict[str, Any], change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    with connect() as conn:
        session, live = _run(conn, config, lock=True)
        if session['status'] == 'closed':
            raise ValueError(CLOSED)
        project, document = session['project_id'], session['target_document_id']
        current = conn.execute(WORKING + ' FOR UPDATE', (session['id'],)).fetchone()
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
                  {'session_id': session['id'], 'turn': session['turns'] + 1})
            draft_id = current['id']
        else:
            previous = conn.execute('''SELECT status,edited FROM ka.document_drafts WHERE session_id=%s
                ORDER BY created_at DESC,id DESC LIMIT 1''', (session['id'],)).fetchone()
            if previous is not None:
                note = (EDITED if previous['status'] == 'open'
                        else (PROMOTED if previous['status'] == 'promoted' else DISCARDED).format(version=live['version']))
            content = {key: live[key] for key in ('purpose', 'audience', 'tags')} | text.model_dump()
            draft_id = create_draft(conn, project, None, content, origin='agent', document_id=document,
                                    session_id=session['id'])
        base_version = conn.execute('SELECT base_version_id FROM ka.document_drafts WHERE id=%s', (draft_id,)).fetchone()
        record_run_sources(conn, project, draft_id, document, base_version['base_version_id'], session['id'],
                           session['turns'] + 1)
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


async def get_doc(context, section: str | None = None, start_line: int | None = None, end_line: int | None = None) -> str:
    return await asyncio.to_thread(_get_doc, context['config'], section, start_line, end_line)


async def save_doc(context, markdown: str, title: str | None = None, description: str | None = None) -> str:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        return {'title': base['title'] if title is None else title,
                'description': base['description'] if description is None else description, 'markdown': markdown}
    return await asyncio.to_thread(_save, context['config'], change)


async def edit_doc(context, old_text: str, new_text: str) -> str:
    return await asyncio.to_thread(_save, context['config'], _replace(old_text, new_text))
