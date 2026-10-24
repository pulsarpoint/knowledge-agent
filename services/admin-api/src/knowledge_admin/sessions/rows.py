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

SessionPurpose = Literal['documents', 'test', 'event', 'resource', 'revision', 'chat']

SELECTION = ('code_project_ids', 'environment_ids', 'service_ids', 'server_ids')


class Selection(BaseModel):
    """What a generation documents: each id once, in the order chosen."""

    model_config = ConfigDict(extra='forbid')
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=20)
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


# The session's pending wake-ups by due time (capabilities/wakeups.py): the detail and the live state (sessions/hub.py).
PENDING_WAKEUPS = """(SELECT coalesce(jsonb_agg(jsonb_build_object('id',i.id,'due_at',i.not_before,'message',i.text,
        'created_at',i.created_at) ORDER BY i.not_before,i.id),'[]') FROM ka.agent_session_inputs i
        WHERE i.session_id=s.id AND i.kind='wakeup' AND i.status='queued') AS wakeups"""


def _select(extra: str = '') -> str:
    return f"""SELECT s.id,s.title,s.agent_id,coalesce(a.name, '{AD_HOC_NAME}') AS agent_name,m.name AS model_name,s.status,s.status_message,s.turns,
        s.input_tokens,s.output_tokens,s.cached_tokens,s.purpose,s.tags,s.model_calls,s.reasoning_tokens,s.document_id,d.title AS document_title,s.folder,
        coalesce(s.lease_expires_at > now(), false) AS worker_active,
        (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued'
        AND i.kind='message') AS queued,
        s.created_at,s.last_activity_at,s.closed_at,{SESSION_DRAFT}{extra}
        FROM ka.agent_sessions s LEFT JOIN ka.agents a ON a.id=s.agent_id JOIN ka.llm_models m ON m.id=s.model_id
        LEFT JOIN ka.document_details d ON d.id=s.document_id"""


SUMMARY = _select()
DETAIL = _select(
    ',s.document_name,s.draft,s.draft_turn,s.prompt_snapshot,s.context_snapshot,s.model_id,s.server_id,s.target_document_id'
    f',{PENDING_WAKEUPS}'
    # what the run was given ([{type, id}]: what the person chose, an environment rather than what it brings);
    # the same entries as sessions/run_access.given
    ",(SELECT coalesce(jsonb_agg(jsonb_build_object('type',r->>'type','id',r->>'id')),'[]') FROM"
    " jsonb_array_elements(s.access->'resources') r WHERE r->>'source'='given') AS given_resources"
    # the catalog tools the run switched off (its agent's and its request's): Start new with these settings keeps them
    ",coalesce(s.agent_config->'disabled_tools','[]'::jsonb) AS disabled_tools"
)


def session_detail(conn, project: UUID, session_id: UUID) -> dict:
    row = conn.execute(DETAIL + ' WHERE s.id=%s AND s.project_id=%s', (session_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, 'Session not found.')
    row['inputs'] = conn.execute(
        """SELECT seq,text,status,turn,created_at FROM ka.agent_session_inputs
        WHERE session_id=%s AND kind='message' ORDER BY seq""",
        (session_id,),
    ).fetchall()
    row['tool_counts'], row['tool_counts_through_id'] = tool_counts_through(
        conn, [f'cc:{session_id}:{turn}' for turn in range(1, row['turns'] + 1)]
    )
    ticket = conn.execute(
        """SELECT e.subject,e.source_id,e.payload->>'issue_id' AS issue_id FROM ka.events e
        WHERE e.source='jira' AND e.id=(SELECT event_id FROM ka.agent_sessions WHERE id=%s)""",
        (session_id,),
    ).fetchone()
    if ticket is not None:  # a run started by a Jira event: the other runs on the same ticket, newest first
        row['ticket_key'] = ticket['subject']
        row['related_runs'] = conn.execute(
            """SELECT s.id AS session_id,s.created_at AS started_at,s.agent_id,coalesce(a.name,%(ad_hoc)s) AS agent_name,
            s.status,e.type AS event_type
            FROM ka.agent_sessions s JOIN ka.events e ON e.id=s.event_id LEFT JOIN ka.agents a ON a.id=s.agent_id
            WHERE s.project_id=%(project)s AND s.id<>%(session)s AND e.source='jira' AND e.source_id=%(connection)s
            AND e.payload->>'issue_id'=%(issue)s ORDER BY s.created_at DESC,s.id LIMIT 20""",
            {
                'ad_hoc': AD_HOC_NAME,
                'project': project,
                'session': session_id,
                'connection': ticket['source_id'],
                'issue': ticket['issue_id'],
            },
        ).fetchall()
    row['started_by'], row['previous_output'] = started_by(conn, session_id)
    pinned_shown = row['purpose'] in ('test', 'event', 'chat', 'revision') or (
        row['purpose'] == 'documents' and 'new_document' in (row['context_snapshot'] or {})
    )  # a new-document run
    if pinned_shown:  # its pinned configuration (credential references only) and, for Test sessions, its suggestions
        row['config'] = conn.execute(
            'SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (session_id,)
        ).fetchone()['agent_config']
        row['reviews'] = conn.execute(
            """SELECT id,comment,suggestion,response_turn,created_at,context->'changes' AS changes,
            context->'memory_notes' AS memory_notes FROM ka.agent_session_reviews
            WHERE session_id=%s ORDER BY created_at,id""",
            (session_id,),
        ).fetchall()
    return row


def started_by(conn, session_id: UUID) -> tuple[dict | None, dict | None]:
    """A handler run: its schedule and handler with their names (None when deleted), and the run whose output its task
    included (events/previous_output.py); (None, None) for other runs."""
    row = conn.execute(
        """SELECT s.schedule_id,c.name AS schedule_name,s.handler_id,h.name AS handler_name,p.id AS previous_id,
        p.last_activity_at AS previous_at FROM ka.agent_sessions s
        LEFT JOIN ka.schedules c ON c.id=s.schedule_id LEFT JOIN ka.event_handlers h ON h.id=s.handler_id
        LEFT JOIN ka.agent_sessions p ON p.id::text=s.agent_config->>'previous_output_from' WHERE s.id=%s""",
        (session_id,),
    ).fetchone()
    by = None
    if row['handler_id'] is not None:
        by = {key: row[key] for key in ('schedule_id', 'schedule_name', 'handler_id', 'handler_name')}
    previous = None
    if row['previous_id'] is not None:
        previous = {'session_id': row['previous_id'], 'finished_at': row['previous_at']}
    return by, previous


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
