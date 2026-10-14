"""0102: the seeded Server discovery prompt carries what code used to add (rewritten only when nobody changed it); open
discovery runs from before are closed, and their proposals stay reviewable."""

from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from discovery_world import world  # noqa: F401  (fixture)
from session_rows import a_model
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0102_discovery_runs.up.sql', '0102_discovery_runs.down.sql'
CLOSED = 'Closed by an update; start a new chat.'
ACTIVE = """SELECT p.revision,p.next_version,v.version,v.content,
    (SELECT count(*) FROM ka.documentation_prompt_versions x WHERE x.prompt_id=p.id)::int AS versions
    FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
    WHERE p.project_id=%s AND p.name='Server discovery'"""
V2_START = 'Explore the server named in the context and propose what the inventory should record about it.'
V1_START = 'Explore this server and propose what the inventory should record about it.'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def active(conn, project):
    return conn.execute(ACTIVE, (project,)).fetchone()


def before_0102(conn, store):
    """A workspace made before 0102: its prompt is 0097's version 1."""
    conn.commit()  # the migration files carry their own BEGIN/COMMIT
    run(conn, DOWN)
    project = store.project(conn, f'shop-{uuid4().hex[:6]}')
    conn.commit()
    return project


def test_up_rewrites_the_untouched_prompt_to_version_2(store):
    with store.connect() as conn:
        project = before_0102(conn, store)
        prompt = active(conn, project)
        assert (prompt['version'], prompt['versions'], prompt['next_version'], prompt['revision']) == (1, 1, 2, 1)
        assert prompt['content'].startswith(V1_START)
        run(conn, UP)
        prompt = active(conn, project)
    assert (prompt['version'], prompt['versions'], prompt['next_version'], prompt['revision']) == (2, 2, 3, 2)
    assert prompt['content'].startswith(V2_START)
    for words in (
        'get_server_info_basic',
        'ssh_run on that server',
        'Never copy passwords',
        'When a proposal is refused',
        'Finish with a short report',
    ):
        assert words in prompt['content']


def test_a_new_workspace_gets_version_2s_text_as_version_1(store):
    with store.connect() as conn:
        project = store.project(conn, f'shop-{uuid4().hex[:6]}')
        prompt = active(conn, project)
    assert (prompt['version'], prompt['versions'], prompt['next_version'], prompt['revision']) == (1, 1, 2, 1)
    assert prompt['content'].startswith(V2_START)


def test_down_restores_version_1_and_up_rewrites_again(store):
    with store.connect() as conn:
        project = before_0102(conn, store)
        run(conn, UP)
        run(conn, DOWN)
        prompt = active(conn, project)
        assert (prompt['version'], prompt['versions'], prompt['next_version'], prompt['revision']) == (1, 1, 2, 1)
        assert prompt['content'].startswith(V1_START)
        fresh = store.project(conn, f'shop-{uuid4().hex[:6]}')
        assert active(conn, fresh)['content'].startswith(V1_START)  # 0097's seed is back
        conn.commit()
        run(conn, UP)
        assert active(conn, project)['content'].startswith(V2_START)
        assert active(conn, fresh)['content'].startswith(V2_START)


def test_a_customized_prompt_is_left_alone_both_ways(store):
    with store.connect() as conn:
        project = before_0102(conn, store)
        conn.execute(
            "UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Server discovery'",
            (project,),
        )
        conn.commit()
        run(conn, UP)
        prompt = active(conn, project)
        assert (prompt['version'], prompt['versions'], prompt['revision']) == (1, 1, 2)
        assert prompt['content'].startswith(V1_START)
        conn.execute(
            "UPDATE ka.documentation_prompts SET revision=1 WHERE project_id=%s AND name='Server discovery'", (project,)
        )
        conn.commit()
        run(conn, UP)
        assert active(conn, project)['version'] == 2
        conn.execute(
            "UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Server discovery'",
            (project,),
        )
        conn.commit()
        run(conn, DOWN)  # version 2 was touched: it stays
        prompt = active(conn, project)
        assert (prompt['version'], prompt['versions']) == (2, 2)


def test_up_closes_open_discovery_runs_whose_proposals_stay_listed(world):
    store = world.store
    with store.connect() as conn:
        conn.commit()
        run(conn, '0106_resource_runs.down.sql')  # discovery runs as 0102 saw them
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        model = a_model(conn, actor)
        runs = {}
        for status in ('working', 'waiting', 'closed'):
            runs[status] = conn.execute(
                """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,purpose,server_id,agent_config,status,closed_at)
                VALUES (gen_random_uuid(),%s,NULL,%s,%s,'Discovery','{}','{}','discovery',%s,%s,%s,CASE WHEN %s='closed' THEN now() END) RETURNING id""",
                (world.project, actor, model, world.server['id'], Jsonb({}), status, status),
            ).fetchone()['id']
            conn.execute(
                """INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text,status)
                VALUES (%s,1,%s,'Next.',%s)""",
                (runs[status], actor, 'queued' if status != 'closed' else 'discarded'),
            )
        conn.execute(
            """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload)
            VALUES (%s,%s,%s,1,'service','{"name":"redis"}')""",
            (world.project, runs['working'], world.server['id']),
        )
        conn.commit()
        run(conn, DOWN)
        run(conn, UP)
        run(conn, '0106_resource_runs.up.sql')  # today's routes see resource runs
        rows = {
            row['id']: row
            for row in conn.execute(
                'SELECT id,status,status_message FROM ka.agent_sessions WHERE id=ANY(%s)', (list(runs.values()),)
            )
        }
        queued = {
            row['session_id']: row['status']
            for row in conn.execute(
                'SELECT session_id,status FROM ka.agent_session_inputs WHERE session_id=ANY(%s)', (list(runs.values()),)
            )
        }
    for status in ('working', 'waiting'):
        assert rows[runs[status]]['status'] == 'closed' and rows[runs[status]]['status_message'] == CLOSED
        assert queued[runs[status]] == 'discarded'
    assert rows[runs['closed']]['status_message'] != CLOSED
    listed = world.client.get(f'/api/discovery/{runs["working"]}/proposals', headers=HEADERS)
    assert listed.status_code == 200, listed.text
    assert [item['payload']['name'] for item in listed.json()['proposals']] == ['redis']
