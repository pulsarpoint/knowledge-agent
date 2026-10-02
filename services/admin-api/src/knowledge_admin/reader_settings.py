"""Administrator choice of the agent that answers Reader users."""
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.access import request_actor
from knowledge_admin.agents import get_agent_config
from knowledge_admin.reader_policy import knowledge_violations


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    knowledge_agent_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1, strict=True)


def violations(agent_id: str, conninfo: str) -> list[str]:
    try:
        return knowledge_violations(get_agent_config(agent_id, conninfo=conninfo))
    except LookupError:
        raise
    except ValueError as exc:
        return [str(exc)]


def build_api(app_name: str, store) -> APIRouter:
    router = APIRouter(prefix='/reader/settings')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    def read() -> dict:
        with database().connect() as conn:
            row = conn.execute('SELECT knowledge_agent_id,revision,updated_at FROM ka.reader_settings').fetchone()
            agents = conn.execute('SELECT id,name,enabled FROM ka.agents WHERE NOT is_coordinator ORDER BY lower(name),id').fetchall()
        candidates = [{**agent, 'violations': violations(agent['id'], store.conninfo)} for agent in agents]
        settings = row or {'knowledge_agent_id': None, 'revision': 0, 'updated_at': None}
        return {**settings, 'candidates': candidates}

    @router.get('')
    def get_settings():
        return read()

    @router.put('')
    def save(value: SettingsInput, request: Request):
        with database().connect() as conn:
            actor = store.actor(conn, *request_actor(request))
            current = conn.execute('SELECT revision FROM ka.reader_settings FOR UPDATE').fetchone()
            if current is not None and current['revision'] != value.revision:
                raise HTTPException(409, 'Reader settings changed. Reload before saving.')
            try:
                reasons = violations(value.knowledge_agent_id, store.conninfo)
            except LookupError:
                raise HTTPException(400, 'Choose an existing agent.') from None
            if reasons:
                raise HTTPException(400, 'This agent cannot answer Reader users: ' + '; '.join(reasons) + '.')
            conn.execute('''INSERT INTO ka.reader_settings(knowledge_agent_id,updated_by) VALUES (%s,%s)
                ON CONFLICT (singleton) DO UPDATE SET knowledge_agent_id=EXCLUDED.knowledge_agent_id,
                revision=ka.reader_settings.revision+1,updated_by=EXCLUDED.updated_by,updated_at=now()''',
                (value.knowledge_agent_id, actor))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                VALUES (%s,%s,'reader.settings.updated','reader_settings',%s)''',
                (store.project(conn, app_name), actor, Jsonb({'knowledge_agent_id': value.knowledge_agent_id})))
        return read()

    return router
