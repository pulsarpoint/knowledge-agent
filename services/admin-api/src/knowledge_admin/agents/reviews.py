"""Tuning reviews of a conversation: the reviewer's output (proposed prompt and instruction texts) and applying it."""

from functools import partial
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.identity import request_actor
from knowledge_admin.agents.clones import NAME_TAKEN, clone
from knowledge_admin.db import storage
from knowledge_admin.knowledge_agents.config import fingerprint, get_agent_config


class InstructionChanges(BaseModel):
    model_config = ConfigDict(extra='forbid')
    system_prompt: str | None = Field(max_length=100000)
    instructions: str | None = Field(max_length=40000)


class TuningSuggestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    suggestion: str = Field(min_length=1)
    changes: InstructionChanges

    @field_validator('changes', mode='before')
    @classmethod
    def decode_changes(cls, value: Any) -> Any:
        # Bedrock structured output can JSON-encode this nested object.
        # Decode it once; InstructionChanges still validates every field.
        return json.loads(value) if isinstance(value, str) else value


REVIEW_NOT_FOUND = 'Suggestion not found for this agent.'
AGENT_CHANGED = 'The agent changed since you opened the suggestion. Review it again.'
PROMPT_REQUIRED = 'Enter a system prompt.'
INVALID_TEXT = 'Instructions cannot contain NUL characters.'
NO_CHANGES = {'system_prompt': None, 'instructions': None}


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: Literal['agent', 'clone']
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)
    changes: InstructionChanges


def write_changes(conn, agent_id: str, changes: InstructionChanges, actor_id) -> None:
    """Write reviewed texts into an agent: its prompt and its instructions. Nothing else changes."""
    if changes.system_prompt is not None:
        conn.execute('UPDATE ka.agents SET system_prompt=%s WHERE id=%s', (changes.system_prompt, agent_id))
    if changes.instructions is not None:
        conn.execute('UPDATE ka.agents SET instructions=%s WHERE id=%s', (changes.instructions, agent_id))
    conn.execute('UPDATE ka.agents SET revision=revision+1,updated_by=%s WHERE id=%s', (actor_id, agent_id))


def build_api(app_name, store):
    router = APIRouter(prefix='/agents/{agent_id}/reviews/{review_id}')

    database = partial(storage, store, 'Agent storage')

    def review_of(conn, agent_id: str, review_id: UUID) -> dict[str, Any]:
        row = conn.execute(
            """SELECT r.suggestion,r.context FROM ka.agent_session_reviews r
            JOIN ka.agent_sessions s ON s.id=r.session_id WHERE r.id=%s AND s.agent_id=%s""",
            (review_id, agent_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, REVIEW_NOT_FOUND)
        return row

    def config(agent_id: str) -> dict[str, Any]:
        try:
            return get_agent_config(agent_id, conninfo=store.conninfo)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('')
    def source(agent_id: str, review_id: UUID, response: Response):
        """The suggestion with the agent's current texts, for the Apply suggestions sheet."""
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            review = review_of(conn, agent_id, review_id)
        current = config(agent_id)
        return {
            'suggestion': review['suggestion'],
            'changes': review['context'].get('changes', NO_CHANGES),
            'revision': current['revision'],
            'fingerprint': fingerprint(current),
            'system_prompt': current['system_prompt']['content'],
            'instructions': current['instructions'],
        }

    @router.post('/apply')
    def apply(agent_id: str, review_id: UUID, value: ApplyInput, request: Request):
        """Write the reviewed texts into this agent, or into a new clone of it (the agent then stays as it was)."""
        changes = value.changes
        if changes.system_prompt is not None and (not changes.system_prompt.strip() or '\x00' in changes.system_prompt):
            raise HTTPException(400, PROMPT_REQUIRED)
        if '\x00' in (changes.instructions or ''):
            raise HTTPException(400, INVALID_TEXT)
        with database().connect() as conn:
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
            review_of(conn, agent_id, review_id)
            current = config(agent_id)
            if current['revision'] != value.revision or fingerprint(current) != value.fingerprint:
                raise HTTPException(409, AGENT_CHANGED)
            actor, project = store.actor(conn, *request_actor(request)), store.project(conn, app_name)
            target = agent_id
            if value.target == 'clone':
                try:
                    target = clone(conn, agent_id, actor)['id']
                except psycopg.errors.UniqueViolation:
                    raise HTTPException(409, NAME_TAKEN) from None
                conn.execute(
                    """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                    VALUES (%s,%s,'agent.cloned','agent',%s)""",
                    (project, actor, Jsonb({'agent_id': target, 'cloned_from': agent_id})),
                )
            write_changes(conn, target, changes, actor)
            conn.execute(
                """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                VALUES (%s,%s,'agent.suggestion.applied','agent',%s)""",
                (
                    project,
                    actor,
                    Jsonb(
                        {
                            'agent_id': target,
                            'source_agent_id': agent_id,
                            'review_id': str(review_id),
                            'target': value.target,
                        }
                    ),
                ),
            )
            return {'agent_id': target}

    return router
