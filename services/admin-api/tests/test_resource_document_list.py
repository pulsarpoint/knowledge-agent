"""A resource's document list (GET /api/inventory/{kind}/{id}/documents and get_resource): the documents of the resource
and of its direct children, each with title, tags, short description and the resource it belongs to (the inventory document, the
one tagged inventory, first); de-duplicated, ordered, live documents only. get_resource lists only documents the run could read."""

from uuid import uuid4

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.documents.store import description_short
from live_documents import link_document, live_document
from test_get_resource import get, given, read
from test_repositories import HEADERS

LONG = 'x' * 170


def entry(document, title, resource_type, resource_id, name, *, tags=(), short='') -> dict:
    return {
        'id': str(document),
        'title': title,
        'tags': list(tags),
        'description_short': short,
        'resource': {'type': resource_type, 'id': str(resource_id), 'name': name},
    }


def documents_of(world) -> dict:
    """Adds: web-1's inventory document (Zeta inventory, tags, a long description), Alpha shared (linked to web-1 and
    nginx), Shop readme (built for Shop), Production notes (the environment) and a draft built for web-1."""
    server = world.server['id']
    with world.store.connect() as conn:
        ids = {
            row['title']: row['id']
            for row in conn.execute('SELECT id,title FROM ka.documents WHERE project_id=%s', (world.project,))
        }
        zeta = live_document(
            conn,
            world.project,
            'zeta',
            'Zeta inventory',
            '# Zeta',
            description=f'  {LONG}\nSecond line',
            tags=['ops', 'linux', 'inventory'],
            server_ids=[server],
        )['id']
        shared = live_document(conn, world.project, 'alpha', 'Alpha shared', '# A', service_ids=[world.nginx])['id']
        link_document(conn, world.project, shared, 'server_id', server)
        readme = live_document(conn, world.project, 'readme', 'Shop readme', '# R', code_project_ids=[world.shop])['id']
        notes = live_document(
            conn, world.project, 'notes', 'Production notes', '# P', environment_ids=[world.environment]
        )['id']
        conn.execute(
            """INSERT INTO ka.documents(project_id,slug,folder,revision,is_draft,title,description,purpose,
            audience,tags,markdown,content_hash,origin)
            VALUES (%s,'web-1-draft',NULL,1,true,'A draft','','explanation','both','{}','# d',
            encode(sha256('# d'),'hex'),'generated') RETURNING id""",
            (world.project,),
        )
        draft = conn.execute("SELECT id FROM ka.documents WHERE slug='web-1-draft'").fetchone()['id']
        link_document(conn, world.project, draft, 'server_id', server, 'built_for')
    return {
        'zeta': zeta,
        'shared': shared,
        'readme': readme,
        'notes': notes,
        'overview': ids['web-1 overview'],
        'runbook': ids['nginx runbook'],
    }


def expected(world, found) -> dict:
    server, nginx, shop, env = world.server['id'], world.nginx, world.shop, world.environment
    zeta_short = 'x' * 160 + '…'
    return {
        'server': [
            entry(
                found['zeta'],
                'Zeta inventory',
                'server',
                server,
                'web-1',
                tags=['ops', 'linux', 'inventory'],
                short=zeta_short,
            ),
            entry(found['shared'], 'Alpha shared', 'server', server, 'web-1'),
            entry(found['overview'], 'web-1 overview', 'server', server, 'web-1', short='About web-1 overview'),
            entry(found['runbook'], 'nginx runbook', 'service', nginx, 'nginx', short='About nginx runbook'),
        ],
        'service': [
            entry(found['shared'], 'Alpha shared', 'service', nginx, 'nginx'),
            entry(found['runbook'], 'nginx runbook', 'service', nginx, 'nginx', short='About nginx runbook'),
            entry(found['readme'], 'Shop readme', 'code_project', shop, 'Shop'),
            entry(found['overview'], 'web-1 overview', 'server', server, 'web-1', short='About web-1 overview'),
            entry(
                found['zeta'],
                'Zeta inventory',
                'server',
                server,
                'web-1',
                tags=['ops', 'linux', 'inventory'],
                short=zeta_short,
            ),
        ],
        'environment': [
            entry(found['notes'], 'Production notes', 'environment', env, 'Production'),
            entry(found['shared'], 'Alpha shared', 'service', nginx, 'nginx'),
            entry(found['runbook'], 'nginx runbook', 'service', nginx, 'nginx', short='About nginx runbook'),
            entry(found['overview'], 'web-1 overview', 'server', server, 'web-1', short='About web-1 overview'),
            entry(
                found['zeta'],
                'Zeta inventory',
                'server',
                server,
                'web-1',
                tags=['ops', 'linux', 'inventory'],
                short=zeta_short,
            ),
        ],
        'code_project': [entry(found['readme'], 'Shop readme', 'code_project', shop, 'Shop')],
    }


def without_connection(items: list[dict]) -> list[dict]:
    return [{key: value for key, value in item.items() if key != 'connection'} for item in items]


def test_description_short_is_the_first_line_cut_at_160():
    assert description_short(None) == '' and description_short('') == ''
    assert description_short('  One line.  \nTwo') == 'One line.'
    assert description_short('\n\nAfter blank lines\nmore') == 'After blank lines'
    assert description_short('y' * 160) == 'y' * 160
    assert description_short('y' * 161) == 'y' * 160 + '…'


def test_each_resource_lists_its_and_its_childrens_documents(world):
    found = documents_of(world)
    want = expected(world, found)
    ids = {
        'server': world.server['id'],
        'service': world.nginx,
        'environment': world.environment,
        'code_project': world.shop,
    }
    for kind, resource_id in ids.items():
        answer = world.client.get(f'/api/inventory/{kind}/{resource_id}/documents', headers=HEADERS).json()
        assert without_connection(answer['documents']) == want[kind], kind
    server = world.client.get(f'/api/inventory/server/{ids["server"]}/documents', headers=HEADERS).json()
    assert server['inventory_document']['id'] == str(found['zeta'])
    assert [item['connection'] for item in server['documents']] == ['built_for', 'linked', 'linked', 'linked']


def test_get_resource_lists_the_same_documents(world):
    found = documents_of(world)
    want = expected(world, found)
    config = given(world)
    server = get(config, 'server', world.server['id'])
    assert without_connection(server['documents']) == want['server']
    assert server['inventory_document'] == {'id': str(found['zeta']), 'title': 'Zeta inventory'}
    assert without_connection(get(config, 'service', world.nginx)['documents']) == want['service']
    assert without_connection(get(config, 'environment', world.environment)['documents']) == want['environment']
    assert without_connection(get(config, 'code_project', world.shop)['documents']) == want['code_project']
    for item in get(config, 'environment', world.environment)['documents']:
        assert read(config, item['id'])['title'] == item['title']


def test_get_resource_lists_only_documents_the_run_could_read(world):
    found = documents_of(world)
    # The environment alone: its children's documents are linked only to resources the run was not given.
    alone = given(world, servers=False, services=False, code=False)
    listed = get(alone, 'environment', world.environment)['documents']
    assert [item['title'] for item in listed] == ['Production notes']
    assert read(alone, str(found['runbook'])) == 'This document is not available in this run.'
    # web-1 without nginx: nginx's runbook is left out; Alpha shared (also linked to web-1) stays under web-1.
    no_services = given(world, services=False)
    titles = [item['title'] for item in get(no_services, 'server', world.server['id'])['documents']]
    assert titles == ['Zeta inventory', 'Alpha shared', 'web-1 overview']
    # A document the run was given directly is readable, so it is listed.
    runbook = {'type': 'document', 'id': str(found['runbook']), 'source': 'given'}
    direct = {**alone, 'access': {**alone['access'], 'resources': [*alone['access']['resources'], runbook]}}
    titles = [item['title'] for item in get(direct, 'environment', world.environment)['documents']]
    assert titles == ['Production notes', 'nginx runbook']


def test_an_unknown_resource_is_404(world):
    assert world.client.get(f'/api/inventory/server/{uuid4()}/documents', headers=HEADERS).status_code == 404
