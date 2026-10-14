"""0095: connections between inventory entries replace the stored system graph."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from old_documents import DOCUMENT_MODEL, before_0104  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0095_inventory_links.up.sql', '0095_inventory_links.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


pytestmark = pytest.mark.usefixtures('before_0104')


def inventory(conn, project):
    ids = {}
    for name in ('api', 'worker'):
        ids[name] = conn.execute(
            'INSERT INTO ka.services(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
        ).fetchone()['id']
    ids['prod-1'] = conn.execute(
        "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'prod-1',%s,false) RETURNING id",
        (project, f'{uuid4().hex[:8]}.internal'),
    ).fetchone()['id']
    ids['production'] = conn.execute(
        "INSERT INTO ka.environments(project_id,name) VALUES (%s,'production') RETURNING id", (project,)
    ).fetchone()['id']
    ids['repo'] = conn.execute(
        """INSERT INTO ka.repositories(project_id,slug,name,remote_url,requested_ref,checkout_path)
        VALUES (%s,%s,'api-code','https://example.test/api.git','main',%s) RETURNING id""",
        (project, f'api-code-{uuid4().hex[:6]}', f'r-{uuid4().hex[:8]}'),
    ).fetchone()['id']
    ids['doc'], version = uuid4(), uuid4()
    conn.execute(
        'INSERT INTO ka.documents(id,project_id,slug,current_version_id) VALUES (%s,%s,%s,%s)',
        (ids['doc'], project, f'runbook-{uuid4().hex[:6]}', version),
    )
    conn.execute(
        """INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
        markdown,content_hash,source_snapshot,origin) VALUES (%s,%s,%s,1,'Runbook','','runbook','both',
        '# Runbook',encode(sha256(convert_to('# Runbook','UTF8')),'hex'),'{}','manual')""",
        (version, ids['doc'], project),
    )
    return ids


def link(conn, project, kind, start, end, **extra):
    columns = {'project_id': project, 'kind': kind, f'from_{start[0]}_id': start[1], f'to_{end[0]}_id': end[1], **extra}
    return conn.execute(
        f'INSERT INTO ka.inventory_links({",".join(columns)}) VALUES ({",".join(["%s"] * len(columns))}) RETURNING *',
        list(columns.values()),
    ).fetchone()


def test_links_have_one_end_each_side_no_self_links_and_no_duplicates(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        row = link(
            conn,
            project,
            'connects_to',
            ('service', ids['api']),
            ('server', ids['prod-1']),
            attributes=Jsonb({'port': '5432'}),
        )
        assert (row['from_kind'], row['from_id'], row['to_kind'], row['to_id']) == (
            'service',
            ids['api'],
            'server',
            ids['prod-1'],
        )
    for start, end, error in [
        (('service', ids['api']), ('server', ids['prod-1']), psycopg.errors.UniqueViolation),
        (('service', ids['api']), ('service', ids['api']), psycopg.errors.CheckViolation),
        (('service', uuid4()), ('server', ids['prod-1']), psycopg.errors.ForeignKeyViolation),
    ]:
        with pytest.raises(error), store.connect() as conn:
            link(conn, project, 'connects_to', start, end)
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.inventory_links(project_id,kind,from_service_id,from_server_id,to_environment_id)
            VALUES (%s,'connects_to',%s,%s,%s)""",
            (project, ids['api'], ids['prod-1'], ids['production']),
        )
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        link(conn, project, 'calls', ('service', ids['api']), ('service', ids['worker']))


def test_deleting_an_entry_removes_its_links_and_attachments(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        link(conn, project, 'connects_to', ('server', ids['prod-1']), ('environment', ids['production']))
        conn.execute(
            'INSERT INTO ka.inventory_documents(project_id,server_id,document_id) VALUES (%s,%s,%s)',
            (project, ids['prod-1'], ids['doc']),
        )
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (ids['prod-1'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_links').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_documents').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.documents WHERE id=%s', (ids['doc'],)).fetchone()['n'] == 1


def test_up_moves_dependencies_code_projects_and_attachments(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        run(conn, DOWN)
        conn.execute(
            "INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id,description) VALUES (%s,%s,%s,'stores orders')",
            (project, ids['api'], ids['worker']),
        )
        conn.execute(
            'INSERT INTO ka.service_repositories(project_id,service_id,repository_id) VALUES (%s,%s,%s)',
            (project, ids['api'], ids['repo']),
        )
        deployment = conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id)
            VALUES (%s,%s,%s,'production',%s) RETURNING id""",
            (project, ids['worker'], ids['prod-1'], ids['production']),
        ).fetchone()['id']

        def node(name, layer, **links):
            columns = ['project_id', 'layer', 'kind', 'name', *links]
            return conn.execute(
                f'INSERT INTO ka.kg_nodes({",".join(columns)}) VALUES ({",".join(["%s"] * len(columns))}) RETURNING id',
                (project, layer, 'x', name, *links.values()),
            ).fetchone()['id']

        env_node = node('production', 'infrastructure', environment_id=ids['production'])
        copy = conn.execute(
            """INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id,server_id,environment_id)
            VALUES (%s,'infrastructure','server','prod-1',%s,%s,%s) RETURNING id""",
            (project, env_node, ids['prod-1'], ids['production']),
        ).fetchone()['id']
        attached = {
            env_node: 'environment',
            copy: 'server',
            node('worker · production', 'infrastructure', deployment_id=deployment): 'service',
            node('api-code', 'software', repository_id=ids['repo']): 'code_project',
            node('Checkout', 'business'): None,
        }
        for node_id in attached:
            conn.execute(
                'INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)',
                (node_id, ids['doc'], project),
            )
        run(conn, UP)
        links = {
            (r['kind'], r['from_id'], r['to_id'], r['description'])
            for r in conn.execute('SELECT * FROM ka.inventory_links')
        }
        assert links == {
            ('depends_on', ids['api'], ids['worker'], 'stores orders'),
            ('built_from', ids['api'], ids['repo'], ''),
        }
        moved = {
            (r['entry_kind'], r['entry_id'])
            for r in conn.execute('SELECT entry_kind,entry_id FROM ka.inventory_documents')
        }
        assert moved == {
            ('environment', ids['production']),
            ('server', ids['prod-1']),
            ('service', ids['worker']),
            ('code_project', ids['repo']),
        }
        assert conn.execute(
            "SELECT to_regclass('ka.kg_nodes') IS NULL AND to_regclass('ka.service_dependencies') IS NULL AS gone"
        ).fetchone()['gone']
        api = conn.execute(
            'SELECT dependencies,repositories,links FROM ka.service_assets WHERE id=%s', (ids['api'],)
        ).fetchone()
        assert [d['service_id'] for d in api['dependencies']] == [str(ids['worker'])]
        assert [r['id'] for r in api['repositories']] == [str(ids['repo'])]
        assert api['links'] == []


def test_down_refuses_what_the_old_schema_cannot_hold(store):
    for setup in ('connects_to', 'attributes', 'built_from_description', 'attachment'):
        with store.connect() as conn:
            project = store.project(conn, 'shop')
            ids = inventory(conn, project)
            if setup == 'connects_to':
                link(conn, project, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']))
            elif setup == 'attributes':
                link(
                    conn,
                    project,
                    'depends_on',
                    ('service', ids['api']),
                    ('service', ids['worker']),
                    attributes=Jsonb({'protocol': 'grpc'}),
                )
            elif setup == 'built_from_description':
                link(
                    conn,
                    project,
                    'built_from',
                    ('service', ids['api']),
                    ('code_project', ids['repo']),
                    description='main repo',
                )
            else:
                conn.execute(
                    'INSERT INTO ka.inventory_documents(project_id,service_id,document_id) VALUES (%s,%s,%s)',
                    (project, ids['api'], ids['doc']),
                )
            with pytest.raises(psycopg.errors.RaiseException, match='Cannot roll back 0095'):
                run(conn, DOWN)
            conn.rollback()


def test_down_and_up_keep_dependencies_and_code_projects(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        link(conn, project, 'depends_on', ('service', ids['api']), ('service', ids['worker']), description='calls it')
        link(conn, project, 'built_from', ('service', ids['api']), ('code_project', ids['repo']))
        run(conn, DOWN)
        assert conn.execute(
            'SELECT service_id,depends_on_service_id,description FROM ka.service_dependencies'
        ).fetchall() == [{'service_id': ids['api'], 'depends_on_service_id': ids['worker'], 'description': 'calls it'}]
        assert conn.execute('SELECT service_id,repository_id FROM ka.service_repositories').fetchall() == [
            {'service_id': ids['api'], 'repository_id': ids['repo']}
        ]
        assert conn.execute(
            "SELECT to_regclass('ka.kg_nodes') IS NOT NULL AND to_regclass('ka.inventory_links') IS NULL AS back"
        ).fetchone()['back']
        run(conn, UP)
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_links').fetchone()['n'] == 2
        run(conn, DOCUMENT_MODEL[1])


def test_service_assets_lists_connects_to_links_from_both_sides(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        out = link(
            conn,
            project,
            'connects_to',
            ('service', ids['api']),
            ('server', ids['prod-1']),
            attributes=Jsonb({'port': '5432'}),
        )
        into = link(conn, project, 'connects_to', ('service', ids['worker']), ('service', ids['api']))
        link(conn, project, 'depends_on', ('service', ids['api']), ('service', ids['worker']))
        api = conn.execute('SELECT links FROM ka.service_assets WHERE id=%s', (ids['api'],)).fetchone()['links']
        assert api == [
            {
                'id': str(out['id']),
                'direction': 'outgoing',
                'kind': 'connects_to',
                'entry_kind': 'server',
                'entry_id': str(ids['prod-1']),
                'name': 'prod-1',
                'description': '',
                'attributes': {'port': '5432'},
            },
            {
                'id': str(into['id']),
                'direction': 'incoming',
                'kind': 'connects_to',
                'entry_kind': 'service',
                'entry_id': str(ids['worker']),
                'name': 'worker',
                'description': '',
                'attributes': {},
            },
        ]


def test_link_kinds_only_join_the_entries_they_are_for(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        link(conn, project, 'depends_on', ('service', ids['api']), ('service', ids['worker']))
        link(conn, project, 'built_from', ('service', ids['api']), ('code_project', ids['repo']))
        link(conn, project, 'connects_to', ('server', ids['prod-1']), ('environment', ids['production']))
    for kind, start, end in [
        ('depends_on', ('server', ids['prod-1']), ('environment', ids['production'])),
        ('depends_on', ('service', ids['api']), ('code_project', ids['repo'])),
        ('built_from', ('service', ids['api']), ('server', ids['prod-1'])),
        ('built_from', ('code_project', ids['repo']), ('service', ids['api'])),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation, match='inventory_links_kind_ends'), store.connect() as conn:
            link(conn, project, kind, start, end)
