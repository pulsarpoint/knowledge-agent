"""Test sessions: the agent Test tab's conversations, run by agent-session-worker like native Generate documents
sessions (ka.agent_sessions_cc with purpose 'test'). Here: creating one (the first message becomes input 1) and listing
an agent's. Everything else — detail, messages, Stop, Close, the event history and stream — is the session API in
claude_code_api."""
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.claude_code import agent_system_prompt, close_idle
from knowledge_admin.claude_code_api import session_detail
from knowledge_admin.knowledge_agents.config import (get_agent_config, model_fits, per_run, require_native,
                                                     require_run_resources)
from knowledge_admin.knowledge_agents.run_resources import RunResourceError, RunResources, resolve

NO_MODEL = 'Select an enabled model in this agent’s General tab.'
SESSION_NOT_FOUND = 'Session not found for this agent.'
NO_SUBJECTS = {'folders': [], 'code_project_ids': []}  # a Test session documents nothing and opens no folder


class TestSessionInput(BaseModel):
    """Test tab → the first message: a new Test session with the agent's current configuration (a per-run agent's
    narrowed to the resources given here), pinned for the whole session."""
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=20000)
    run_resources: RunResources | None = None

    @field_validator('message')
    @classmethod
    def valid_message(cls, value: str) -> str:
        if '\x00' in value or not value.strip():
            raise ValueError('Enter a message without null characters')
        return value.strip()


def build_api(app_name, store):
    router = APIRouter(prefix='/agents')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent session storage is not configured.')
        return store

    @router.post('/{agent_id}/sessions', status_code=201)
    def create(agent_id: str, value: TestSessionInput, request: Request) -> dict:
        db = database()
        try:
            config = get_agent_config(agent_id, conninfo=db.conninfo)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            try:
                require_native(config)
                pinned = resolve(conn, project, config, value.run_resources)
                require_run_resources(pinned)
                if not pinned['enabled']:
                    raise ValueError('Agent is disabled.')
                model = pinned['model']
                if model is None or not model['enabled'] or not model_fits('openai', model):
                    raise ValueError(NO_MODEL)
            except RunResourceError as exc:  # a ValueError: caught first
                raise HTTPException(400, str(exc)) from None
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            session_id = uuid4()
            # The worker decrypts per turn: the pinned config holds credential references only.
            conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,purpose,runtime,agent_config,run_resources)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'test','openai',%s,%s)""",
                (session_id, project, agent_id, actor, model['id'], value.message[:200],
                 Jsonb({'system_prompt': agent_system_prompt(pinned)}), Jsonb(NO_SUBJECTS),
                 Jsonb(jsonable_encoder(pinned)), Jsonb(pinned['run_resources']) if per_run(config) else None))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, value.message))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent.test_session.created','agent_session',%s,%s)''',
                (project, actor, session_id, Jsonb({'agent_id': agent_id})))
            return session_detail(conn, project, session_id)

    @router.get('/{agent_id}/sessions')
    def sessions(agent_id: str, response: Response) -> dict:
        """This agent's Test sessions, newest first."""
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            return {'sessions': conn.execute('''SELECT s.id,s.title,s.status,s.created_at,s.last_activity_at,
                p.email AS actor_email FROM ka.agent_sessions_cc s JOIN ka.principals p ON p.id=s.actor_id
                WHERE s.agent_id=%s AND s.project_id=%s AND s.purpose='test'
                ORDER BY s.created_at DESC,s.id LIMIT 100''', (agent_id, db.project(conn, app_name))).fetchall()}

    return router
