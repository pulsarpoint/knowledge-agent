"""The shared search-agent API, with conversations scoped to the signed-in user."""
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from docs_wiki import audit, sessions
from docs_wiki.agent_context import AgentContext
from docs_wiki.code_client import CodeAgentError
from docs_wiki.rag_client import RagError
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
              audit_dir: Path | None = None, profiles: bool = False) -> APIRouter:
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

    @router.post('/chat/{key}/escalate')
    async def escalate(key: str, body: ChatInput, request: Request):
        conversation(request, key)
        if not body.question.strip():
            raise HTTPException(400, 'Enter a question')
        return PlainTextResponse('TODO')

    return router
