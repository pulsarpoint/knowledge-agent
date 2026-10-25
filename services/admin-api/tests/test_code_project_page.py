"""A code project's page: chats started from the code project (resource runs tagged code_projects/<id>), its page data
(GET /api/code-projects/{id}: the services built from it, recent sync jobs, its code graph state) and the seeded
"Code project overview" prompt (0140) the Discover wizard offers for code projects."""

from pathlib import Path
from uuid import uuid4

from access_helpers import ids, of
from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.sessions.start import RESOURCE_TAG
from test_discovery_runs import pinned_of
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
NAME = 'Code project overview'


def start(world, started_from: dict, **change):
    body = {
        'started_from': started_from,
        'model_id': world.model,
        'system_prompt': 'You answer.',
        'message': 'What is this?',
        **change,
    }
    return world.client.post('/api/sessions', headers=HEADERS, json=body)


def with_folder(world):
    (world.data / 'code' / 'shop').mkdir()
    with world.store.connect() as conn:
        conn.execute(
            "INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,'shop')",
            (world.shop, world.project),
        )


def test_a_chat_on_a_code_project_is_a_resource_run_that_holds_it_and_its_folders(world):
    with_folder(world)
    created = start(world, {'code_project_id': str(world.shop)})
    assert created.status_code == 201, created.text
    sid = created.json()['id']
    with world.store.connect() as conn:
        row = conn.execute(
            'SELECT purpose,tags,server_id,context_snapshot FROM ka.agent_sessions WHERE id=%s', (sid,)
        ).fetchone()
    assert row['purpose'] == 'resource' and row['tags'] == [f'code_projects/{world.shop}']
    assert row['server_id'] is None and row['context_snapshot'] == {'code_project_id': str(world.shop)}
    pinned = pinned_of(world, sid)
    assert pinned['started_from'] == {'type': 'code_project', 'id': str(world.shop), 'name': 'Shop'}
    assert created.json()['given_resources'] == [{'type': 'code_project', 'id': str(world.shop)}]
    assert ids(pinned, 'code_project') == [str(world.shop)]
    assert of(pinned, 'code_project')[0]['folder_paths'] == [str(world.data / 'code' / 'shop')]
    assert ids(pinned, 'service') == [] and ids(pinned, 'server') == []  # a code project brings nothing
    listed = world.client.get('/api/sessions', headers=HEADERS, params={'tag': f'code_projects/{world.shop}'})
    assert [run['id'] for run in listed.json()['sessions']] == [sid]


def test_a_missing_or_archived_code_project_is_not_found_and_its_tag_comes_only_from_it(world):
    missing = start(world, {'code_project_id': str(uuid4())})
    assert missing.status_code == 404 and missing.json()['error']['message'] == 'Code project not found.'
    other = start(world, {'service_id': str(world.nginx)}, tags=[f'code_projects/{world.shop}'])
    assert other.status_code == 400
    assert RESOURCE_TAG.format(tag=f'code_projects/{world.shop}') in other.json()['error']['message']
    both = start(world, {'code_project_id': str(world.shop), 'service_id': str(world.nginx)})
    assert both.status_code == 400
    with world.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (world.shop,))
    assert start(world, {'code_project_id': str(world.shop)}).status_code == 404


def test_options_for_a_code_project_offer_its_prompts_and_mark_it_linked(world):
    response = world.client.get(f'/api/sessions/options?code_project_id={world.shop}', headers=HEADERS)
    assert response.status_code == 200, response.text
    options = response.json()
    assert [prompt['name'] for prompt in options['prompts']] == [NAME]
    assert [item['id'] for item in options['resources']['code_projects'] if item['linked']] == [str(world.shop)]
    assert world.client.get(f'/api/sessions/options?code_project_id={uuid4()}', headers=HEADERS).status_code == 404
    both = world.client.get(
        f'/api/sessions/options?code_project_id={world.shop}&service_id={world.nginx}', headers=HEADERS
    )
    assert both.status_code == 400
    server = world.client.get(f'/api/sessions/options?server_id={world.server["id"]}', headers=HEADERS).json()
    assert NAME not in [prompt['name'] for prompt in server['prompts']]


def test_a_new_document_can_be_built_for_a_code_project(world):
    new = {'folder': 'code', 'name': 'Shop notes', 'code_project_id': str(world.shop)}
    created = start(world, {'new_document': new}, resources={'documents': [{'new': new}]})
    assert created.status_code == 201, created.text
    pinned = pinned_of(world, created.json()['id'])
    assert ids(pinned, 'code_project') == [str(world.shop)]
    document = next(item for item in pinned['access']['resources'] if item['type'] == 'document')
    assert document['built_for'] == [['code_project', str(world.shop)]]


def test_the_page_data_has_the_services_built_from_it_recent_syncs_and_its_graph(world):
    with world.store.connect() as conn:
        for minutes, status in ((2, 'failed'), (1, 'succeeded')):
            conn.execute(
                """INSERT INTO ka.jobs(project_id,repository_id,kind,status,error,created_at,started_at,finished_at)
                VALUES (%s,%s,'repository_sync',%s,%s,now()-make_interval(mins=>%s),now(),now())""",
                (world.project, world.shop, status, 'boom' if status == 'failed' else None, minutes),
            )
    response = world.client.get(f'/api/code-projects/{world.shop}', headers=HEADERS)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page['id'] == str(world.shop) and page['name'] == 'Shop'
    assert page['remote_url'] == 'https://git.example/shop.git' and page['folder_paths'] == []
    assert page['built_into'] == [{'id': str(world.nginx), 'name': 'nginx'}]
    assert [job['status'] for job in page['sync_jobs']] == ['succeeded', 'failed']
    assert page['sync_jobs'][1]['error'] == 'boom' and page['sync_status'] == 'succeeded'
    assert page['processing'] is None and page['code_index'] is None
    assert world.client.get(f'/api/code-projects/{uuid4()}', headers=HEADERS).status_code == 404
    with world.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (world.shop,))
    assert world.client.get(f'/api/code-projects/{world.shop}', headers=HEADERS).status_code == 404


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def seeded(conn, project):
    return conn.execute(
        """SELECT p.id,p.input_kind,p.tags,v.content FROM ka.documentation_prompts p
        JOIN ka.documentation_prompt_versions v ON v.prompt_id=p.id WHERE p.project_id=%s AND p.name=%s""",
        (project, NAME),
    ).fetchall()


def test_every_workspace_and_every_new_one_has_the_overview_prompt_once(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')  # created after the migration: the trigger seeds it
        conn.execute('SELECT ka.seed_code_project_overview_prompt(%s)', (project,))  # again: still one
        rows = seeded(conn, project)
    assert len(rows) == 1
    assert rows[0]['input_kind'] == 'code' and rows[0]['tags'] == ['code-project', 'overview']
    content = rows[0]['content']
    assert 'new_document' in content and 'update_document' in content
    for section in ('Purpose', 'Structure', 'How to run and build', 'Configuration', 'Dependencies', 'Deployment'):
        assert section in content
    assert 'Where to look first' in content


def test_down_removes_only_the_unchanged_prompt(store):
    with store.connect() as conn:
        shop = store.project(conn, 'shop')
        edited = store.project(conn, 'edited')
        prompt = seeded(conn, edited)[0]['id']
        conn.execute(
            """INSERT INTO ka.documentation_prompt_versions(prompt_id,version,content)
            VALUES (%s,2,'Mine.')""",
            (prompt,),
        )
        conn.execute('UPDATE ka.documentation_prompts SET next_version=3 WHERE id=%s', (prompt,))
        run(conn, '0140_code_project_prompt.down.sql')
        assert seeded(conn, shop) == []
        assert len(seeded(conn, edited)) == 2
        run(conn, '0140_code_project_prompt.up.sql')  # up again: workspaces get it back
        assert len(seeded(conn, shop)) == 1
