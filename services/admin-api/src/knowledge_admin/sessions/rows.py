"""What a session looks like to the API: its row, its detail and its events."""

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_admin.documents.drafts import SESSION_DRAFT
from knowledge_admin.knowledge_agents.config import AD_HOC_NAME
from knowledge_admin.sessions.run_record import tool_counts_through

NO_MODEL = 'Choose an enabled model.'
CLOSED = 'This session is closed. Start a new session.'
NOTHING_TO_STOP = 'The agent is not working, so there is nothing to stop.'

SessionPurpose = Literal['documents', 'test', 'event', 'resource', 'revision']

SELECTION = ('code_project_ids', 'collection_ids', 'environment_ids', 'service_ids', 'server_ids')


class Selection(BaseModel):
    """What a generation documents: each id once, in the order chosen."""

    model_config = ConfigDict(extra='forbid')
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=20)
    collection_ids: list[UUID] = Field(default_factory=list, max_length=50)
    environment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    service_ids: list[UUID] = Field(default_factory=list, max_length=50)
    server_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def once(self):
        for name in SELECTION:
            setattr(self, name, list(dict.fromkeys(getattr(self, name))))
        return self


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=20000)

    @field_validator('text')
    @classmethod
    def valid_text(cls, value: str) -> str:
        if '\x00' in value or not value.strip():
            raise ValueError('Enter a message without null characters')
        return value.strip()


def _select(extra: str = '') -> str:
    return f"""SELECT s.id,s.title,s.agent_id,coalesce(a.name, '{AD_HOC_NAME}') AS agent_name,m.name AS model_name,s.status,s.status_message,s.turns,
        s.input_tokens,s.output_tokens,s.cached_tokens,s.purpose,s.tags,s.model_calls,s.reasoning_tokens,s.document_id,d.title AS document_title,s.folder,
        coalesce(s.lease_expires_at > now(), false) AS worker_active,
        (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued,
        s.created_at,s.last_activity_at,s.closed_at,{SESSION_DRAFT}{extra}
        FROM ka.agent_sessions s LEFT JOIN ka.agents a ON a.id=s.agent_id JOIN ka.llm_models m ON m.id=s.model_id
        LEFT JOIN ka.document_details d ON d.id=s.document_id"""


SUMMARY = _select()
DETAIL = _select(
    ',s.document_name,s.draft,s.draft_turn,s.prompt_snapshot,s.context_snapshot,s.model_id,s.server_id,s.target_document_id'
    # what the run was given ([{type, id}]: what the person chose, an environment rather than what it brings)
    ",(SELECT coalesce(jsonb_agg(jsonb_build_object('type',r->>'type','id',r->>'id')),'[]') FROM"
    " jsonb_array_elements(s.access->'resources') r WHERE r->>'source'='given') AS given_resources"
)


def session_detail(conn, project: UUID, session_id: UUID) -> dict:
    row = conn.execute(DETAIL + ' WHERE s.id=%s AND s.project_id=%s', (session_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, 'Session not found.')
    row['inputs'] = conn.execute(
        """SELECT seq,text,status,turn,created_at FROM ka.agent_session_inputs
        WHERE session_id=%s ORDER BY seq""",
        (session_id,),
    ).fetchall()
    row['tool_counts'], row['tool_counts_through_id'] = tool_counts_through(
        conn, [f'cc:{session_id}:{turn}' for turn in range(1, row['turns'] + 1)]
    )
    pinned_shown = row['purpose'] in ('test', 'event', 'revision') or (
        row['purpose'] == 'documents' and 'new_document' in (row['context_snapshot'] or {})
    )  # a new-document run
    if pinned_shown:  # its pinned configuration (credential references only) and, for Test sessions, its suggestions
        row['config'] = conn.execute(
            'SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (session_id,)
        ).fetchone()['agent_config']
        row['reviews'] = conn.execute(
            """SELECT id,comment,suggestion,response_turn,created_at,context->'changes' AS changes
            FROM ka.agent_session_reviews WHERE session_id=%s ORDER BY created_at,id""",
            (session_id,),
        ).fetchall()
    return row


def stream_event(row: dict) -> dict:
    """One run event for the page; a tool result keeps its preview, the full output stays in the run record."""
    payload = dict(row['payload'])
    if row['kind'] == 'tool_result':
        payload.pop('output', None)
    return {
        'id': row['id'],
        'turn': int(row['run_key'].rsplit(':', 1)[1]),
        'seq': row['seq'],
        'kind': row['kind'],
        'payload': payload,
        'created_at': row['created_at'],
    }
