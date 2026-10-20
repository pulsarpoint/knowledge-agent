"""Tuning reviews of a conversation: the reviewer's output (proposed instructions and memory notes) and applying it.
A review never changes the system prompt."""

from functools import partial
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_admin.identity import request_actor
from knowledge_admin.agents.clones import NAME_TAKEN, clone
from knowledge_admin.db import storage
from knowledge_admin.knowledge_agents.config import fingerprint, saved_config
from knowledge_admin.memory import store as memory
from knowledge_admin.memory.api import EXISTS
from knowledge_admin.memory.embedding import current_model, embed_text

MAX_NOTES = 10


def decoded(value: Any) -> Any:
    # Bedrock structured output can JSON-encode a nested value. Decode it once; the model still validates it.
    return json.loads(value) if isinstance(value, str) else value


class InstructionChanges(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instructions: str | None = Field(max_length=40000)


class MemoryNote(BaseModel):
    """A proposed new shared memory entry; name, metadata and content follow the memory module's rules."""

    model_config = ConfigDict(extra='forbid')
    name: str
    content: str
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def follows_memory_rules(self) -> 'MemoryNote':
        memory.validate(self.name, self.metadata, self.content)
        return self


class TuningSuggestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    suggestion: str = Field(min_length=1)
    changes: InstructionChanges
    memory_notes: list[MemoryNote] = Field(max_length=MAX_NOTES)

    _decode = field_validator('changes', 'memory_notes', mode='before')(decoded)


REVIEW_NOT_FOUND = 'Suggestion not found for this agent.'
AGENT_CHANGED = 'The agent changed since you opened the suggestion. Review it again.'
INVALID_TEXT = 'Instructions cannot contain NUL characters.'


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: Literal['agent', 'clone']
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)
    instructions: str | None = Field(default=None, max_length=40000)
    memory_notes: list[MemoryNote] = Field(default_factory=list, max_length=MAX_NOTES)


def write_changes(conn, agent_id: str, instructions: str, actor_id) -> None:
    """Write reviewed instructions into an agent. Nothing else changes."""
    conn.execute(
        'UPDATE ka.agents SET instructions=%s,revision=revision+1,updated_by=%s WHERE id=%s',
        (instructions, actor_id, agent_id),
    )


def build_api(app_name, store):
    router = APIRouter(prefix='/agents/{agent_id}/reviews/{review_id}')

    database = partial(storage, store, 'Agent storage')

    def review_of(conn, agent_id: str, review_id: UUID) -> dict[str, Any]:
        row = conn.execute(
            """SELECT r.session_id,r.suggestion,r.context FROM ka.agent_session_reviews r
            JOIN ka.agent_sessions s ON s.id=r.session_id WHERE r.id=%s AND s.agent_id=%s""",
            (review_id, agent_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, REVIEW_NOT_FOUND)
        return row

    def config(conn, agent_id: str) -> dict[str, Any]:
        try:
            return saved_config(conn, agent_id)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None

    @router.get('')
    def source(agent_id: str, review_id: UUID, response: Response):
        """The suggestion with the agent's current instructions and, per proposed memory note, the entry that already
        has its name (if any), for the Apply suggestions sheet."""
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            review = review_of(conn, agent_id, review_id)
            current = config(conn, agent_id)
            project = store.project(conn, app_name)
            notes = []
            for note in review['context'].get('memory_notes', []):
                entry = memory.get(conn, project, name=note['name'])
                notes.append(
                    {
                        **note,
                        'exists': entry is not None,
                        'memory_id': entry and entry['id'],
                        'current_content': entry and entry['content'],
                    }
                )
        return {
            'suggestion': review['suggestion'],
            'changes': {'instructions': review['context'].get('changes', {}).get('instructions')},
            'memory_notes': notes,
            'revision': current['revision'],
            'fingerprint': fingerprint(current),
            'instructions': current['instructions'],
        }

    @router.post('/apply')
    def apply(agent_id: str, review_id: UUID, value: ApplyInput, request: Request):
        """Write the reviewed instructions into this agent, or into a new clone of it (the agent then stays as it
        was), and save the ticked memory notes as new shared memory entries. A note whose name is taken is refused by
        name; the other notes and the instructions still apply. Notes alone skip the agent-changed check: they do not
        modify the agent."""
        if '\x00' in (value.instructions or ''):
            raise HTTPException(400, INVALID_TEXT)
        with database().connect() as conn:  # embedded before the transaction, like the memory API
            review_of(conn, agent_id, review_id)
            model = current_model(conn, store.project(conn, app_name)) if value.memory_notes else None
        embedded = [embed_text(model, note.content) for note in value.memory_notes]
        with database().connect() as conn:
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
            review = review_of(conn, agent_id, review_id)
            actor, project = store.actor(conn, *request_actor(request)), store.project(conn, app_name)
            target, cloned = agent_id, False
            if value.instructions is not None:
                current = config(conn, agent_id)
                if current['revision'] != value.revision or fingerprint(current) != value.fingerprint:
                    raise HTTPException(409, AGENT_CHANGED)
                if value.target == 'clone':
                    try:
                        target = clone(conn, agent_id, actor)['id']
                    except psycopg.errors.UniqueViolation:
                        raise HTTPException(409, NAME_TAKEN) from None
                    cloned = True
                    conn.execute(
                        """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                        VALUES (%s,%s,'agent.cloned','agent',%s)""",
                        (project, actor, Jsonb({'agent_id': target, 'cloned_from': agent_id})),
                    )
                write_changes(conn, target, value.instructions, actor)
            author = memory.Author(session_id=review['session_id'], actor_id=actor)
            saved, refused = [], []
            for note, (vector, profile) in zip(value.memory_notes, embedded):
                metadata = {**note.metadata, 'source': 'review'}
                try:
                    entry = memory.create(conn, project, note.name, metadata, note.content, author, vector, profile)
                except memory.Exists:
                    refused.append({'name': note.name, 'message': EXISTS.format(name=note.name)})
                except memory.Invalid as error:  # e.g. source: review is one key too many
                    refused.append({'name': note.name, 'message': str(error)})
                else:
                    saved.append({'id': entry['id'], 'name': entry['name']})
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
                            'target': 'clone' if cloned else 'agent',
                            'instructions_changed': value.instructions is not None,
                            'memory_saved': [row['name'] for row in saved],
                            'memory_refused': [row['name'] for row in refused],
                        }
                    ),
                ),
            )
            return {'agent_id': target, 'saved': saved, 'refused': refused}

    return router
