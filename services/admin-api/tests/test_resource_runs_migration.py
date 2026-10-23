"""0106: discovery runs become resource runs tagged with their server (listed among the server's chats), sessions get
tags, proposals may have no server, and the three assistant prompts are seeded; down restores discovery runs and
refuses what it cannot keep (service and environment runs, customized assistant prompts)."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from discovery_world import world  # noqa: F401  (fixture)
from session_rows import a_model
from test_discovery_runs import ssh_ready
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0106_resource_runs.up.sql', '0106_resource_runs.down.sql'
ASSISTANTS = """SELECT name FROM ka.documentation_prompts WHERE project_id=%s AND 'assistant'=ANY(tags) ORDER BY name"""
TOOLS = [
    {'tool': 'ssh_run', 'mode': 'on'},
    {'tool': 'propose_document', 'mode': 'on'},
    {'tool': 'propose_service', 'mode': 'on'},
    {'tool': 'save_document', 'mode': 'off'},
]


LATER = '0115_tools_always_on.down.sql'  # ka.agent_tools comes back (empty): 0106 deletes old tools' rows


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture(autouse=True)
def tools_table(world):
    with world.store.connect() as conn:
        run(conn, LATER)


def discovery_run(conn, world, status='waiting'):
    """A discovery run as 0105 stored it: purpose discovery, its server pinned as started_from_server."""
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    config = {
        'started_from_server': {'id': world.server['id'], 'name': 'web-1'},
        'tools': {'items': TOOLS},
        'ssh': {'enabled': True, 'servers': []},
    }
    return conn.execute(
        """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,purpose,server_id,agent_config,status)
        VALUES (gen_random_uuid(),%s,NULL,%s,%s,'Discovery','{}','{}','discovery',%s,%s,%s) RETURNING id""",
        (world.project, actor, a_model(conn, actor), world.server['id'], Jsonb(config), status),
    ).fetchone()['id']


def test_discovery_runs_become_resource_runs_listed_among_their_servers_chats(world):
    with world.store.connect() as conn:
        conn.commit()
        run(conn, DOWN)
        old = discovery_run(conn, world)
        conn.commit()
        run(conn, UP)
        row = conn.execute('SELECT purpose,tags,server_id,agent_config FROM ka.agent_sessions WHERE id=%s', (old,))
        row = row.fetchone()
    assert row['purpose'] == 'resource' and row['tags'] == [f'servers/{world.server["id"]}']
    assert str(row['server_id']) == world.server['id']
    assert row['agent_config']['started_from'] == {'type': 'server', 'id': world.server['id'], 'name': 'web-1'}
    assert row['agent_config']['proposal_tools'] == ['propose_service', 'propose_document']
    assert 'started_from_server' not in row['agent_config']
    listed = world.client.get('/api/sessions', headers=HEADERS, params={'tag': f'servers/{world.server["id"]}'})
    assert [item['id'] for item in listed.json()['sessions']] == [str(old)]


def test_a_discovery_run_from_before_started_from_server_gets_its_server_and_its_documents_accept(world):
    """A 0097-0101 discovery run (closed by 0102) has no started_from_server: up builds its started_from from server_id,
    so a document proposal of it becomes a draft for its server; down removes what up added."""
    with world.store.connect() as conn:
        conn.commit()
        run(conn, DOWN)
        old = discovery_run(conn, world)
        conn.execute(
            """UPDATE ka.agent_sessions SET agent_config=agent_config-'started_from_server',status='closed',
            closed_at=now() WHERE id=%s""",
            (old,),
        )
        conn.commit()
        run(conn, UP)
        config = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (old,)).fetchone()
        proposal = conn.execute(
            """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload)
            VALUES (%s,%s,%s,1,'document',%s) RETURNING id,revision""",
            (
                world.project,
                old,
                world.server['id'],
                Jsonb({'title': 'Disk layout', 'description': '', 'markdown': '# D'}),
            ),
        ).fetchone()
        conn.commit()
    assert config['agent_config']['started_from'] == {'type': 'server', 'id': world.server['id'], 'name': 'web-1'}
    accepted = world.client.post(
        f'/api/discovery/proposals/{proposal["id"]}/accept', headers=HEADERS, json={'revision': proposal['revision']}
    )
    assert accepted.status_code == 200, accepted.text
    with world.store.connect() as conn:
        draft = conn.execute('SELECT folder FROM ka.documents WHERE id=%s', (accepted.json()['result_id'],)).fetchone()
        links = conn.execute(
            'SELECT server_id FROM ka.document_resources WHERE document_id=%s', (accepted.json()['result_id'],)
        ).fetchall()
        conn.commit()
        run(conn, DOWN)
        restored = conn.execute('SELECT purpose,agent_config FROM ka.agent_sessions WHERE id=%s', (old,)).fetchone()
        conn.commit()
        run(conn, UP)
    assert draft['folder'] == 'servers/web-1' and [str(link['server_id']) for link in links] == [world.server['id']]
    assert restored['purpose'] == 'discovery'
    assert not {'started_from', 'started_from_server', 'started_from_backfilled'} & set(restored['agent_config'])


def test_down_restores_discovery_runs_and_up_again_round_trips(world):
    ssh_ready(world)
    created = world.client.post(
        '/api/sessions',
        headers=HEADERS,
        json={
            'started_from': {'server_id': world.server['id']},
            'model_id': world.model,
            'system_prompt': 'You answer.',
            'message': 'Disk?',
            'tags': ['proposals'],
        },
    )
    assert created.status_code == 201, created.text
    sid = created.json()['id']
    with world.store.connect() as conn:
        conn.commit()
        run(conn, DOWN)
        row = conn.execute('SELECT purpose,agent_config FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()
        assert row['purpose'] == 'discovery'
        assert row['agent_config']['started_from_server'] == {'id': world.server['id'], 'name': 'web-1'}
        assert 'started_from' not in row['agent_config'] and 'proposal_tools' not in row['agent_config']
        assert conn.execute(ASSISTANTS, (world.project,)).fetchall() == []
        columns = {
            item['column_name']
            for item in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='agent_sessions'"
            ).fetchall()
        }
        assert 'tags' not in columns
        fresh = world.store.project(conn, f'shop-{uuid4().hex[:6]}')  # no trigger after down
        assert conn.execute(ASSISTANTS, (fresh,)).fetchall() == []
        conn.commit()
        run(conn, UP)
        row = conn.execute('SELECT purpose,tags,agent_config FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()
        assert [item['name'] for item in conn.execute(ASSISTANTS, (fresh,)).fetchall()] == [
            'Environment assistant',
            'Server assistant',
            'Service assistant',
        ]
    assert row['purpose'] == 'resource' and row['tags'] == [f'servers/{world.server["id"]}']
    assert row['agent_config']['proposal_tools'] == []  # built from tool rows, which runs no longer have or read


def test_up_seeds_the_assistant_prompts_for_existing_workspaces(world):
    with world.store.connect() as conn:
        conn.commit()
        run(conn, DOWN)
        project = world.store.project(conn, f'shop-{uuid4().hex[:6]}')
        conn.commit()
        run(conn, UP)
        names = [item['name'] for item in conn.execute(ASSISTANTS, (project,)).fetchall()]
    assert names == ['Environment assistant', 'Server assistant', 'Service assistant']


def test_down_refuses_a_customized_assistant_prompt(world):
    with world.store.connect() as conn:
        conn.execute(
            "UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Service assistant'",
            (world.project,),
        )
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Service assistant prompt has been customized'):
            run(conn, DOWN)


def test_down_refuses_service_and_environment_runs(world):
    created = world.client.post(
        '/api/sessions',
        headers=HEADERS,
        json={
            'started_from': {'service_id': str(world.nginx)},
            'model_id': world.model,
            'system_prompt': 'You answer.',
            'message': 'Alive?',
        },
    )
    assert created.status_code == 201, created.text
    with world.store.connect() as conn:
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='started from a service or an environment'):
            run(conn, DOWN)


OLD_TOOLS = ('get_server_info_basic', 'get_server_services', 'get_server_docs', 'get_service_info', 'get_service_docs')
DISCOVERY = """SELECT p.revision,p.next_version,v.version,v.content,
    (SELECT count(*) FROM ka.documentation_prompt_versions x WHERE x.prompt_id=p.id)::int AS versions
    FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
    WHERE p.project_id=%s AND p.name='Server discovery'"""


def agent_tools(conn, agent_id) -> dict[str, str]:
    rows = conn.execute('SELECT tool,mode FROM ka.agent_tools WHERE agent_id=%s', (agent_id,)).fetchall()
    return {row['tool']: row['mode'] for row in rows}


def test_up_removes_the_old_inventory_tools_rows(world):
    with world.store.connect() as conn:
        conn.commit()
        run(conn, DOWN)
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='operations'")
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES ('operations','get_server_docs','on')")
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES ('operations','ssh_run','on')")
        conn.commit()
        run(conn, UP)
        assert agent_tools(conn, 'operations') == {'ssh_run': 'on'}  # get_resource is on in runs, without a row
        count = conn.execute('SELECT count(*) AS n FROM ka.agent_tools WHERE tool=ANY(%s)', (list(OLD_TOOLS),))
        assert count.fetchone()['n'] == 0
        conn.commit()
        run(conn, DOWN)
        assert agent_tools(conn, 'operations') == {'ssh_run': 'on'}


def test_the_assistant_and_server_discovery_prompts_name_get_resource(world):
    with world.store.connect() as conn:
        prompts = conn.execute(
            """SELECT p.name,v.content FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id WHERE p.project_id=%s
            AND (p.name='Server discovery' OR 'assistant'=ANY(p.tags))""",
            (world.project,),
        ).fetchall()
    assert len(prompts) == 4
    for prompt in prompts:
        assert 'get_resource' in prompt['content'], prompt['name']
        assert 'get_server_' not in prompt['content'] and 'get_service_' not in prompt['content']


def test_an_unedited_server_discovery_prompt_gets_a_new_version_and_down_restores_it(world):
    with world.store.connect() as conn:
        conn.commit()
        run(conn, DOWN)
        project = world.store.project(conn, f'shop-{uuid4().hex[:6]}')  # 0102's text as version 1
        edited = world.store.project(conn, f'shop-{uuid4().hex[:6]}')
        conn.execute(
            "UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Server discovery'",
            (edited,),
        )
        before = conn.execute(DISCOVERY, (project,)).fetchone()
        assert 'get_server_info_basic' in before['content']
        conn.commit()
        run(conn, UP)
        after = conn.execute(DISCOVERY, (project,)).fetchone()
        assert (after['version'], after['versions'], after['next_version'], after['revision']) == (2, 2, 3, 2)
        assert 'get_resource' in after['content'] and 'get_server_info_basic' not in after['content']
        assert 'get_server_info_basic' in conn.execute(DISCOVERY, (edited,)).fetchone()['content']
        conn.commit()
        run(conn, DOWN)
        restored = conn.execute(DISCOVERY, (project,)).fetchone()
        assert {key: restored[key] for key in ('version', 'versions', 'next_version', 'revision')} == {
            key: before[key] for key in ('version', 'versions', 'next_version', 'revision')
        }
        assert restored['content'] == before['content']
