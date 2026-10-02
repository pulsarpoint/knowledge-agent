"""The shared search-agent API, with conversations scoped to the signed-in user."""
import hashlib
import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from docs_wiki import audit, gaps, sessions
from docs_wiki.agent_context import AgentContext
from docs_wiki.code_client import CodeAgentError
from docs_wiki.rag_client import RagError
from docs_wiki.reader_client import UNAVAILABLE, ReaderError
from knowledge_llm import ModelUnavailable, managed
from knowledge_llm.profiles import load_profile


class Sources(BaseModel):
    model_config = ConfigDict(extra='forbid')
    docs: StrictBool = True
    codex: StrictBool = True


class ChatInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=10000)
    session: str | None = None
    sources: Sources = Field(default_factory=Sources)
    context: AgentContext = Field(default_factory=AgentContext)


def build_api(prefix: str, state_root: Path, rag, code,
              audit_dir: Path | None = None, profiles: bool = False,
              reader=None, gaps_dir: Path | None = None) -> APIRouter:
    router = APIRouter(prefix=prefix)

    def selected_profile(request: Request):
        if not managed():
            return None
        # The reader application always uses the default search profile.
        agent_id = request.query_params.get('agent_id', 'search') if profiles else 'search'
        try:
            return load_profile(agent_id)
        except ModelUnavailable as exc:
            raise HTTPException(409, str(exc)) from None

    def folder_allowed(row, selected):
        return selected is None or selected['documents_enabled'] and (selected['all_folders'] or any(
            row['path'] == path or selected['include_subfolders'] and row['path'].startswith(path + '/')
            for path in selected['folders']))

    def repository_allowed(row, selected):
        return selected is None or selected['repositories_enabled'] and (selected['all_repositories'] or row['name'] in selected['repository_ids'])

    def user_state(request: Request) -> tuple[Path, str]:
        subject = request.headers.get('x-forwarded-user')
        if not subject:
            raise HTTPException(403, 'Authenticated user identity required')
        identity = hashlib.sha256(subject.encode()).hexdigest()
        selected = selected_profile(request)
        root = state_root if selected is None or selected['id'] == 'search' else state_root / hashlib.sha256(selected['id'].encode()).hexdigest()
        return root / identity, request.headers.get('x-forwarded-email', subject)

    def conversation(request: Request, key: str) -> Path:
        directory, _ = user_state(request)
        if not sessions.KEY_RE.fullmatch(key) or not sessions.exists(directory, key):
            raise HTTPException(404, 'Unknown conversation')
        return directory

    @router.get('/status')
    def status(request: Request):
        selected = selected_profile(request)
        return {'name': selected['name'] if selected else 'Search agent',
                'ask_enabled': True, 'ask_reason': None, 'model': None,
                'sources': {'docs': rag is not None and (selected is None or selected['documents_enabled']),
                            'codex': code is not None and (selected is None or selected['repositories_enabled'])}}

    @router.get('/context')
    def available_context(request: Request):
        user_state(request)
        selected = selected_profile(request)
        result = {'folders': [], 'repositories': [], 'warnings': []}
        if rag is not None:
            try:
                result['folders'] = [{'path': row['path'], 'description': row.get('description', ''),
                                      'indexed_files': row.get('indexed_files', 0)} for row in rag.folders() if folder_allowed(row, selected)]
            except RagError:
                result['warnings'].append('Document folders are unavailable. Try refreshing the context list.')
        if code is not None:
            try:
                result['repositories'] = [{'id': row['name'], 'name': row.get('label') or row['name'],
                                           'synced': row.get('path_exists', False)} for row in code.repos() if repository_allowed(row, selected)]
            except CodeAgentError:
                result['warnings'].append('Git repositories are unavailable. Try refreshing the context list.')
        return result

    if reader is None:
        @router.get('/chats/{key}')
        async def transcript(key: str, request: Request):
            directory = conversation(request, key)
            return {'session': key, 'messages': await sessions.transcript(directory, key),
                    **(sessions.context(directory, key) or {'context': AgentContext().model_dump(),
                                                           'sources': {'docs': True, 'codex': code is not None}})}

        @router.delete('/chats/{key}', status_code=204)
        async def delete(key: str, request: Request):
            await sessions.delete(conversation(request, key), key)
            return Response(status_code=204)

        @router.get('/chats')
        def history(request: Request):
            directory, email = user_state(request)
            return {'sessions': [row for row in audit.sessions_for(audit_dir or directory, email)
                                 if sessions.exists(directory, row['session_key'])][:50]}

        @router.post('/chat')
        async def chat(body: ChatInput, request: Request):
            user_state(request)
            if not body.question.strip():
                raise HTTPException(400, 'Enter a question')
            if body.session is not None:
                conversation(request, body.session)
            return PlainTextResponse('TODO')

    def identity(request: Request) -> tuple[str, str]:
        subject = request.headers.get('x-forwarded-user')
        if not subject:
            raise HTTPException(403, 'Authenticated user identity required')
        return subject, request.headers.get('x-forwarded-email', subject)

    def frame(name: str, data: dict) -> str:
        return f'event: {name}\ndata: {json.dumps(data)}\n\n'

    def session_ref(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def failure(exc: ReaderError) -> JSONResponse:
        return JSONResponse({'error': {'message': exc.message}}, status_code=exc.status)

    def known(key: str) -> bool:
        """Reader conversations are admin-api sessions; any other key (e.g. a legacy file key) is unknown."""
        try:
            UUID(key)
        except ValueError:
            return False
        return True

    unknown = ReaderError(404, 'Unknown conversation')

    if reader is not None:
        @router.post('/chat')
        async def reader_chat(body: ChatInput, request: Request):
            subject, email = identity(request)
            if not body.question.strip():
                raise HTTPException(400, 'Enter a question')
            payload = {'question': body.question, 'folders': body.context.folders,
                       'include_subfolders': body.context.include_subfolders}
            if body.session is not None:
                if not known(body.session):
                    return failure(unknown)
                payload['session_id'] = body.session
            try:
                upstream = await reader.run(subject, email, payload)
            except ReaderError as exc:
                return failure(exc)

            async def stream():
                key = body.session
                finished = False
                async for name, data in upstream:
                    if name == 'heartbeat':
                        yield ': heartbeat\n\n'
                    elif name == 'session':
                        key = data['id']
                        yield frame('session', {'key': key})
                    elif name == 'activity':
                        yield frame('tool', {'name': data['tool'], 'argument': data.get('argument', '')})
                    elif name == 'completed':
                        finished = True
                        if data['status'] != 'completed':
                            yield frame('error', {'message': data.get('error') or UNAVAILABLE})
                            break
                        found = data.get('grounding') or {}
                        sources = found.get('sources', [])
                        # Log the gap first: a user who disconnects on `done` must not lose it.
                        if found.get('not_covered') and gaps_dir is not None:
                            gaps.append_gap(gaps_dir, body.question, found.get('near_misses', []),
                                            results=found.get('results'), session_ref=session_ref(key) if key else None)
                        yield frame('sources', {'sources': sources})
                        yield frame('done', {'text': data['answer'], 'not_covered': bool(found.get('not_covered')),
                                             'escalation_offered': False, 'sources_used': ['docs'] if sources else []})
                        break
                if not finished:
                    yield frame('error', {'message': UNAVAILABLE})
            return StreamingResponse(stream(), media_type='text/event-stream',
                                     headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

        @router.get('/chats')
        async def reader_history(request: Request):
            subject, email = identity(request)
            try:
                rows = await reader.sessions(subject, email)
            except ReaderError as exc:
                return failure(exc)
            return {'sessions': [{'session_key': row['id'], 'first_question': row['title'],
                                  'first_ts': row['created_at'], 'last_ts': row['last_activity_at']} for row in rows]}

        @router.get('/chats/{key}')
        async def reader_transcript(key: str, request: Request):
            subject, email = identity(request)
            if not known(key):
                return failure(unknown)
            try:
                saved = await reader.transcript(subject, email, key)
            except ReaderError as exc:
                return failure(exc)
            messages = [message if message['role'] == 'user' else {
                'role': 'assistant', 'content': message['content'],
                'sources_used': ['docs'] if message.get('sources') else [],
                'sources': message.get('sources', []), 'not_covered': message.get('not_covered', False)}
                for message in saved['messages']]
            return {'session': saved['id'], 'messages': messages,
                    'context': {'folders': saved['folders'], 'include_subfolders': saved['include_subfolders'], 'repositories': None},
                    'sources': {'docs': True, 'codex': False}}

        @router.delete('/chats/{key}', status_code=204)
        async def reader_delete(key: str, request: Request):
            subject, email = identity(request)
            if not known(key):
                return failure(unknown)
            try:
                await reader.delete(subject, email, key)
            except ReaderError as exc:
                return failure(exc)
            return Response(status_code=204)

    @router.post('/chat/{key}/escalate')
    async def escalate(key: str, body: ChatInput, request: Request):
        conversation(request, key)
        if not body.question.strip():
            raise HTTPException(400, 'Enter a question')
        return PlainTextResponse('TODO')

    return router
