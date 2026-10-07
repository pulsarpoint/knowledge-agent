"""0072: Claude Code agents are configured like native agents; 0070 registrations get the built-in prompt."""
from pathlib import Path

import psycopg
import pytest

from knowledge_admin.claude_code import BUILT_IN_PROMPT_TITLE, SYSTEM_PROMPT

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0072_knowledge_agents_config.up.sql', '0072_knowledge_agents_config.down.sql'
ALL_TOOLS = ['Read', 'Grep', 'Glob', 'Bash']


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def active_prompt(conn, agent_id: str):
    return conn.execute('''SELECT v.version,v.title,v.content,v.published_at IS NOT NULL AS published,p.next_version
        FROM ka.agent_prompts p JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id WHERE p.agent=%s''',
        (agent_id,)).fetchone()


def has_column(conn, name: str) -> bool:
    return conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name=%s""", (name,)).fetchone() is not None


def test_registrations_get_the_built_in_prompt_and_every_tool(store):
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        conn.execute("INSERT INTO ka.agents(id,name,runtime) VALUES ('writer','Writer','claude_code')")
        conn.commit()
        run(conn, UP)
        conn.commit()
        assert conn.execute("SELECT runtime,claude_code_tools,model_id FROM ka.agents WHERE id='writer'").fetchone() == {
            'runtime': 'claude_code', 'claude_code_tools': ALL_TOOLS, 'model_id': None}
        assert active_prompt(conn, 'writer') == {'version': 1, 'title': BUILT_IN_PROMPT_TITLE, 'content': SYSTEM_PROMPT,
                                                 'published': True, 'next_version': 2}
        assert conn.execute("SELECT claude_code_tools FROM ka.agents WHERE id='search'").fetchone()['claude_code_tools'] == ALL_TOOLS
        assert active_prompt(conn, 'search')['title'] != BUILT_IN_PROMPT_TITLE  # native agents keep their prompts
        assert not has_column(conn, 'prompt_agent')


def test_claude_code_agents_take_every_capability_but_need_a_prompt(store):
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agents SET runtime='claude_code',documents_enabled=true,documents_grounded=true,
            repositories_enabled=true,ssh_enabled=true,skills_enabled=true,mcp_enabled=true,services_enabled=true,
            all_collections=true,all_repositories=true,claude_code_tools='{Read,Grep}' WHERE id='operations'""")
        conn.commit()
        for statement, error in (
                ("UPDATE ka.agents SET claude_code_tools='{Read,Edit}' WHERE id='operations'", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET claude_code_tools='{Read,NULL}' WHERE id='operations'", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET claude_code_tools=NULL WHERE id='operations'", psycopg.errors.NotNullViolation),
                ("UPDATE ka.agents SET runtime='claude_code' WHERE is_coordinator", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET runtime='other' WHERE id='operations'", psycopg.errors.CheckViolation)):
            with pytest.raises(error):
                with conn.transaction():
                    conn.execute(statement)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):  # like every agent, a Claude Code agent needs its prompt
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.agents(id,name,runtime) VALUES ('no-prompt','No prompt','claude_code')")


def test_down_refuses_configured_claude_code_agents_and_keeps_registrations(store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET runtime='claude_code' WHERE id='search'")  # documents on: more than 0070 keeps
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Claude Code agents have a configuration'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agents SET runtime='openai' WHERE id='search'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        conn.execute("INSERT INTO ka.agents(id,name,runtime) VALUES ('writer','Writer','claude_code')")
        conn.commit()
        run(conn, UP)
        conn.commit()
        run(conn, DOWN)  # a registration with only its built-in prompt rolls back to a 0070 registration
        conn.commit()
        assert conn.execute("SELECT runtime,prompt_agent FROM ka.agents WHERE id='writer'").fetchone() == {
            'runtime': 'claude_code', 'prompt_agent': None}
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_prompt_versions WHERE agent='writer'").fetchone()['n'] == 0
        assert not has_column(conn, 'claude_code_tools')
        with pytest.raises(psycopg.errors.CheckViolation, match='claude_code_agent_is_a_registration'):
            with conn.transaction():
                conn.execute("UPDATE ka.agents SET ssh_enabled=true WHERE id='writer'")
        run(conn, UP)
        conn.commit()
