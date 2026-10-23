"""0115: every run gets every tool, so per-agent tool storage goes — ka.agent_tools and ka.tools.needs, the Tools tab
API, the wizard's tool choice, the May start subagents switch and the configuration's tools section."""

from pathlib import Path

import psycopg
import pytest

from knowledge_admin.knowledge_agents.config import SECTIONS, get_agent_config, sections
from test_agent_apply import apply, preview
from test_agent_clones import clone
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_profiles import create, update, wizard_payload
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0115_tools_always_on.up.sql', '0115_tools_always_on.down.sql'
TOOL = "INSERT INTO ka.tools(name,position,tool_group,label,summary,needs) VALUES (%s,%s,'servers','L','S',%s)"


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def table(conn) -> str | None:
    return conn.execute("SELECT to_regclass('ka.agent_tools')::text AS t").fetchone()['t']


def columns(conn, name: str) -> list[str]:
    return [
        row['column_name']
        for row in conn.execute(
            """SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name=%s
            ORDER BY ordinal_position""",
            (name,),
        )
    ]


def test_the_table_and_the_column_are_gone_and_come_back_empty_on_down(store):
    with store.connect() as conn:
        assert table(conn) is None and 'needs' not in columns(conn, 'tools')
        run(conn, DOWN)
        assert table(conn) is not None
        assert columns(conn, 'agent_tools') == ['agent_id', 'tool', 'mode', 'usage', 'subagent_allowed', 'updated_at']
        assert columns(conn, 'tools')[-1] == 'needs'
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tools').fetchone()['n'] == 0
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('operations','read_file')")
        conn.execute(
            "INSERT INTO ka.agent_tools(agent_id,tool,subagent_allowed) VALUES ('operations','expert:x',false)"
        )
        conn.execute(TOOL, ('ssh_run', 0, 'ssh'))
        conn.commit()
        for statement in (
            "INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('operations','read_file')",  # (agent_id, tool) key
            "INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('nobody','read_file')",  # agents foreign key
            "INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES ('operations','list_files','sometimes')",
            "INSERT INTO ka.agent_tools(agent_id,tool,usage) VALUES ('operations','list_files',repeat('x',8001))",
            "INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('operations','run_subagent')",  # subagents_do_not_spawn
            "INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('operations','expert:y')",
        ):
            with pytest.raises(psycopg.errors.IntegrityError):
                conn.execute(statement)
            conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(TOOL, ('read_file', 1, 'toys'))
        conn.rollback()
        run(conn, UP)
        assert table(conn) is None and 'needs' not in columns(conn, 'tools')


def test_the_per_agent_tools_endpoints_are_gone(store, saved_agent):
    client = saved_agent[0]
    assert client.get('/api/agents/operations/tools', headers=HEADERS).status_code == 404
    saved = client.put(
        '/api/agents/operations/tools', headers=HEADERS, json={'expected_revision': 1, 'tools': []}
    ).status_code
    assert saved in (404, 405)


def test_the_wizard_offers_and_takes_no_tools(store, saved_agent):
    client = saved_agent[0]
    assert 'tools' not in client.get('/api/agents/wizard/options', headers=HEADERS).json()
    for field, value in (('tools', ['read_file']), ('subagents', True), ('subagent_blocked', ['shell_run'])):
        refused = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Odd', **{field: value}))
        assert refused.status_code == 400 and 'Extra inputs are not permitted' in refused.text


def test_an_agent_has_no_subagents_switch_and_no_tools_summary(store, saved_agent):
    client = saved_agent[0]
    agent = create(client, 'Lead')
    assert 'subagents' not in agent
    refused = update(client, agent, subagents=True)
    assert refused.status_code == 400 and 'Extra inputs are not permitted' in refused.text
    listed = client.get('/api/agents', headers=HEADERS).json()['agents']
    assert all('tools_summary' not in row for row in listed)


def test_the_configuration_has_no_tools_and_a_clone_round_trips(store, saved_agent):
    client = saved_agent[0]
    original = get_agent_config('operations', conninfo=store.conninfo)
    assert 'tools' not in original and 'tools' not in SECTIONS and 'tools' not in sections(original)
    copy_id = clone(client)['id']
    assert sections(get_agent_config(copy_id, conninfo=store.conninfo)) == sections(original)
    update(client, client.get(f'/api/agents/{copy_id}', headers=HEADERS).json(), instructions='Evidence first.')
    value = preview(client, copy_id)
    assert value['changes'] == ['General']
    assert apply(client, copy_id, value).status_code == 200
    assert get_agent_config('operations', conninfo=store.conninfo)['instructions'] == 'Evidence first.'
