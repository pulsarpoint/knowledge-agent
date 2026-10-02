"""Reader conversations, reachable only from docs-wiki on the internal network."""
import asyncio
import hmac
import json
import os
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from knowledge_admin.agent_sessions import RunInput, expire_sessions, start_turn
from knowledge_admin.reader_policy import NOT_ALLOWED, NOT_CONFIGURED, knowledge_violations

READER_IDLE_TIMEOUT = timedelta(days=7)
ENDED = 'This conversation has ended. Start a new one.'
BUSY = 'Wait for the current answer.'
UNKNOWN = 'Unknown conversation'
FRIENDLY = {'This session already has a running message or tuning review.': BUSY,
            'This session is closed or expired. Start a new session.': ENDED}
FolderPath = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*$', max_length=500)]


class ReaderRunInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID | None = None
    question: str = Field(min_length=1, max_length=10000)
    folders: list[FolderPath] | None = Field(default=None, max_length=200)
    include_subfolders: StrictBool = True


def narrow(config: dict[str, Any], folders: list[str] | None, include_subfolders: bool) -> dict[str, Any]:
    """Limit the agent's document folders to the user's selection; never broaden them."""
    if folders is None:
        return config
    chosen = set(folders)
    def selected(path: str) -> bool:
        return path in chosen or include_subfolders and any(path.startswith(folder + '/') for folder in chosen)
    documents = {**config['documents'], 'all_folders': False, 'folders': [
        folder for folder in config['documents']['folders'] if selected(folder['search_folder'].replace('.', '/'))]}
    return {**config, 'documents': documents}


def validate(config: dict[str, Any]) -> None:
    if not config['enabled'] or config['model'] is None or not config['model']['enabled']:
        raise ValueError(NOT_CONFIGURED)
    if knowledge_violations(config):
        raise ValueError(NOT_ALLOWED)


def build_api(app_name: str, store) -> APIRouter:
    router = APIRouter(prefix='/internal/reader')

    def reader(request: Request) -> tuple[str, str]:
        expected = os.environ.get('READER_AGENT_KEY')
        if store is None or not expected:
            raise HTTPException(503, 'Reader chat is not configured.')
        if not hmac.compare_digest(request.headers.get('x-reader-key', '').encode(), expected.encode()):
            raise HTTPException(401, 'Invalid reader key.')
        subject = request.headers.get('x-reader-user', '')
        if not subject:
            raise HTTPException(403, 'Authenticated user identity required')
        return subject, request.headers.get('x-reader-email', '')

    def owned(conn, actor: tuple[str, str], session_id: UUID) -> dict[str, Any]:
        row = conn.execute('''SELECT s.* FROM ka.agent_sessions s WHERE s.id=%s AND s.project_id=%s AND s.channel='reader'
            AND s.actor_id=%s AND s.hidden_by_owner_at IS NULL''',
            (session_id, store.project(conn, app_name), store.actor(conn, *actor))).fetchone()
        if row is None:
            raise HTTPException(404, UNKNOWN)
        return row

    @router.post('/runs')
    async def run(value: ReaderRunInput, request: Request):
        actor = reader(request)
        await asyncio.to_thread(expire_sessions, store)
        if value.session_id is not None:
            with store.connect() as conn:
                saved = owned(conn, actor, value.session_id)
            if saved['status'] != 'active':
                raise HTTPException(409, ENDED)
            agent_id = saved['agent']
        else:
            with store.connect() as conn:
                settings = conn.execute('SELECT knowledge_agent_id FROM ka.reader_settings').fetchone()
            if settings is None:
                raise HTTPException(409, NOT_CONFIGURED)
            agent_id = settings['knowledge_agent_id']
        turn_input = RunInput(id=value.id, session_id=value.session_id, is_test=False, task=value.question)
        try:
            turn = await start_turn(request.app.state, store, app_name, agent_id, turn_input, actor,
                channel='reader', idle_timeout=READER_IDLE_TIMEOUT, owner_only=True,
                prepare=lambda config: narrow(config, value.folders, value.include_subfolders), validate=validate)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, FRIENDLY.get(exc.detail, exc.detail)) from None

        async def stream():
            async for event, data in turn:
                if event == 'heartbeat':
                    yield ': heartbeat\n\n'
                    continue
                if event == 'started':
                    if value.session_id is None:
                        payload = ('session', {'id': str(data['session_id'])})
                    else:
                        continue
                elif event == 'activity':
                    if data.get('kind') != 'tool' or data.get('status') != 'inProgress':
                        continue
                    payload = ('activity', {'tool': data['title'], 'argument': data.get('detail', '')})
                else:
                    payload = ('completed', {'status': data['status'], 'answer': data['answer'], 'error': data['error'],
                                             'grounding': data['context'].get('grounding')})
                yield 'event: ' + payload[0] + '\ndata: ' + json.dumps(payload[1]) + '\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @router.get('/sessions')
    def sessions(request: Request):
        actor = reader(request)
        with store.connect() as conn:
            rows = conn.execute('''SELECT id,title,status,created_at,last_activity_at FROM ka.agent_sessions
                WHERE project_id=%s AND channel='reader' AND actor_id=%s AND hidden_by_owner_at IS NULL
                ORDER BY last_activity_at DESC LIMIT 50''', (store.project(conn, app_name), store.actor(conn, *actor))).fetchall()
        return {'sessions': rows}

    @router.get('/sessions/{session_id}')
    def transcript(session_id: UUID, request: Request):
        actor = reader(request)
        with store.connect() as conn:
            saved = owned(conn, actor, session_id)
            runs = conn.execute('''SELECT task,answer,status,context->'grounding' AS grounding FROM ka.operations_runs
                WHERE session_id=%s ORDER BY created_at,id''', (session_id,)).fetchall()
        messages = []
        for run in runs:
            messages.append({'role': 'user', 'content': run['task']})
            if run['status'] == 'completed':
                grounding = run['grounding'] or {}
                messages.append({'role': 'assistant', 'content': run['answer'], 'sources': grounding.get('sources', []),
                                 'not_covered': bool(grounding.get('not_covered'))})
        documents = saved['config']['documents']
        folders = None if documents.get('all_folders') else [f['search_folder'].replace('.', '/') for f in documents['folders']]
        return {'id': saved['id'], 'title': saved['title'], 'status': saved['status'], 'folders': folders,
                'include_subfolders': documents.get('include_subfolders', True), 'messages': messages}

    @router.delete('/sessions/{session_id}', status_code=204)
    def delete(session_id: UUID, request: Request):
        actor = reader(request)
        with store.connect() as conn:
            owned(conn, actor, session_id)
            conn.execute('''UPDATE ka.agent_sessions SET status=CASE WHEN status='active' THEN 'closed' ELSE status END,
                closed_at=coalesce(closed_at,now()),hidden_by_owner_at=now() WHERE id=%s''', (session_id,))
            conn.execute("UPDATE ka.operations_runs SET status='cancelled',finished_at=now(),progress='Session closed.' WHERE session_id=%s AND status='running'", (session_id,))
        return Response(status_code=204)

    return router
