"""Rows for tests that need a Test session (ka.agent_sessions, purpose 'test') or a suggestion without running
one."""
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb


def a_model(conn, actor) -> UUID:
    """A model a session row can name: the first saved one, or a new OpenAI Responses model."""
    row = conn.execute('SELECT id FROM ka.llm_models ORDER BY created_at,id LIMIT 1').fetchone()
    if row is not None:
        return row['id']
    return conn.execute("""INSERT INTO ka.llm_models(name,provider,model,api_format,base_url,created_by,updated_by)
        VALUES ('Session rows model','openai','gpt-5','responses','http://model.test/v1',%s,%s) RETURNING id""",
        (actor, actor)).fetchone()['id']


def add_test_session(conn, project, actor, agent_id: str, model_id, *, config: dict[str, Any] | None = None,
                     message: str | None = None, status: str = 'starting', turns: int = 0, purpose: str = 'test') -> UUID:
    """A Test session of the agent as POST /api/sessions with an agent inserts it (or, with purpose 'event', as an event
    handler starts one); `message` queues input 1."""
    session = uuid4()
    conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,purpose,agent_config,status,turns,closed_at)
        VALUES (%s,%s,%s,%s,%s,%s,'{}',%s,%s,%s,%s,%s,CASE WHEN %s='closed' THEN now() END)''',
        (session, project, agent_id, actor, model_id, (message or 'Test chat')[:200],
         Jsonb({'folders': [], 'code_project_ids': []}), purpose, Jsonb(config or {'id': agent_id}), status, turns, status))
    if message is not None:
        conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                     (session, actor, message))
    return session


def add_review(conn, session_id, actor, *, changes: dict[str, Any], agent_config: dict[str, Any] | None = None,
               turn: int | None = None) -> UUID:
    """A saved tuning suggestion of a Test session, as the review endpoint stores it."""
    return conn.execute('''INSERT INTO ka.agent_session_reviews(session_id,actor_id,comment,context,suggestion,
        response_turn) VALUES (%s,%s,'Feedback',%s,'Be clearer.',%s) RETURNING id''',
        (session_id, actor, Jsonb({'agent_config': agent_config or {}, 'changes': changes}), turn)).fetchone()['id']


def add_ad_hoc_session(conn, project, actor, model_id, *, message: str | None = None) -> UUID:
    """A Generate documents session of an ad-hoc agent as POST /api/generations/sessions inserts it (no agents row)."""
    session = uuid4()
    conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,document_name,folder,agent_config)
        VALUES (%s,%s,NULL,%s,%s,'Payments','{}',%s,'Payments','payments',%s)''',
        (session, project, actor, model_id, Jsonb({'folders': [], 'code_project_ids': []}), Jsonb({'id': 'ad-hoc'})))
    if message is not None:
        conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                     (session, actor, message))
    return session
