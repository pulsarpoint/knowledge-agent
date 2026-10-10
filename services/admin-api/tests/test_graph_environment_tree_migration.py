"""0094: a server can have one graph node per environment (server_id + environment_id)."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def rows(conn, project):
    server = conn.execute("INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'prod-1',%s,false) RETURNING id",
                          (project, f'{uuid4().hex[:8]}.internal')).fetchone()['id']
    production = conn.execute("INSERT INTO ka.environments(project_id,name) VALUES (%s,'production') RETURNING id", (project,)).fetchone()['id']
    staging = conn.execute("INSERT INTO ka.environments(project_id,name) VALUES (%s,'staging') RETURNING id", (project,)).fetchone()['id']
    return server, production, staging


def add(conn, project, name, **links):
    columns = ','.join(['project_id', 'layer', 'kind', 'name', *links])
    values = (project, links.pop('layer', 'infrastructure'), 'server', name, *links.values())
    return conn.execute(f"INSERT INTO ka.kg_nodes({columns}) VALUES ({','.join(['%s'] * len(values))}) RETURNING id", values).fetchone()['id']


def test_a_server_has_one_node_per_environment(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        server, production, staging = rows(conn, project)
        add(conn, project, 'prod-1', server_id=server)                            # top level
        add(conn, project, 'prod-1 (p)', server_id=server, environment_id=production)
        add(conn, project, 'prod-1 (s)', server_id=server, environment_id=staging)
        add(conn, project, 'production', environment_id=production)               # the environment's own node
    for links, error in [({'server_id': server}, psycopg.errors.UniqueViolation),                       # second top-level copy
                         ({'server_id': server, 'environment_id': production}, psycopg.errors.UniqueViolation),
                         ({'environment_id': production}, psycopg.errors.UniqueViolation),               # second env node
                         ({'server_id': server, 'service_id': uuid4()}, psycopg.errors.CheckViolation)]:  # other pairs refused
        with pytest.raises(error), store.connect() as conn:
            add(conn, project, f'x-{uuid4().hex[:6]}', **links)


def test_down_and_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        server, production, _ = rows(conn, project)
        top = add(conn, project, 'prod-1', server_id=server)
        copy = add(conn, project, 'prod-1 (2)', server_id=server, environment_id=production)
        run(conn, '0094_graph_environment_tree.down.sql')
        links = {row['id']: (row['server_id'], row['environment_id']) for row in
                 conn.execute('SELECT id,server_id,environment_id FROM ka.kg_nodes').fetchall()}
        assert links == {top: (server, None), copy: (None, None)}  # copies with an environment are unlinked, kept
        run(conn, '0094_graph_environment_tree.up.sql')
