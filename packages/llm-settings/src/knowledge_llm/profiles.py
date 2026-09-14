"""Named agent identity and resource scopes shared by the application runtimes."""
import psycopg
from psycopg.rows import dict_row

from knowledge_llm import ModelUnavailable


def profile(conn, agent_id: str, *, runnable: bool = False) -> dict:
    row = conn.execute('SELECT * FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()
    if row is None:
        raise ModelUnavailable('Agent not found or not supported by this runtime.')
    if runnable and not row['enabled']:
        raise ModelUnavailable('This agent is disabled. Enable it in its settings before running a task.')
    row['folders'] = [r['path'] for r in conn.execute('''SELECT f.path FROM ka.agent_knowledge_folders a
        JOIN ka.rag_folders f ON f.id=a.folder_id WHERE a.agent_id=%s AND f.deleted_at IS NULL ORDER BY f.path''', (agent_id,))]
    row['repository_ids'] = [str(r['repository_id']) for r in conn.execute('SELECT a.repository_id FROM ka.agent_repositories a JOIN ka.repositories r ON r.id=a.repository_id WHERE a.agent_id=%s AND r.archived_at IS NULL ORDER BY a.repository_id', (agent_id,))]
    row['services'] = conn.execute('SELECT s.id,s.name,s.description FROM ka.agent_services a JOIN ka.services s ON s.id=a.service_id WHERE a.agent_id=%s ORDER BY s.name', (agent_id,)).fetchall()
    row['skills'] = conn.execute('''SELECT s.id,s.name,s.description,s.instructions,s.enabled FROM ka.skills_agents a
        JOIN ka.agent_skills s ON s.id=a.skill_id
        WHERE a.agent_id=%s ORDER BY s.name''', (agent_id,)).fetchall()
    return row


def load_profile(agent_id: str) -> dict:
    try:
        with psycopg.connect('', row_factory=dict_row, connect_timeout=5) as conn:
            selected = profile(conn, agent_id, runnable=True)
            if selected['skills_enabled'] and any(not skill['enabled'] for skill in selected['skills']):
                raise ModelUnavailable('An assigned skill is disabled. Update the agent skill selection.')
            return selected
    except psycopg.Error:
        raise ModelUnavailable('Agent configuration is unavailable. Check administration.') from None
