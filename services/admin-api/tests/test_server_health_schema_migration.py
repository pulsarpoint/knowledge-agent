"""0137: the built-in "Server health check" output schema, seeded in every workspace that has none of that name."""

from pathlib import Path

from knowledge_admin.output_schemas.rules import check_schema, validate

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
NAME = 'Server health check'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def seeded(conn, project):
    return conn.execute(
        """SELECT s.current_version, v.version, v.schema FROM ka.output_schemas s
        JOIN ka.output_schema_versions v ON v.schema_id = s.id WHERE s.project_id = %s AND s.name = %s""",
        (project, NAME),
    ).fetchall()


def test_every_workspace_gets_the_schema_once_and_it_is_in_the_supported_subset(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, '0137_server_health_schema.up.sql')
        run(conn, '0137_server_health_schema.up.sql')  # again: still one
        rows = seeded(conn, project)
    assert len(rows) == 1
    assert (rows[0]['current_version'], rows[0]['version']) == (1, 1)
    schema = rows[0]['schema']
    assert check_schema(schema) == []
    normal = {'server': 'app-1', 'overall_status': 'normal', 'summary': 'All good.', 'findings': []}
    issue = {
        'server': 'app-1',
        'overall_status': 'issue',
        'summary': 'The disk is nearly full.',
        'findings': [
            {
                'severity': 'issue',
                'category': 'disk',
                'service': None,
                'title': 'Disk /var at 92% on app-1',
                'details': 'The /var volume is at 92%.',
                'evidence': 'df -h /var → 92%',
                'recommendation': 'Rotate the logs in /var/log.',
            }
        ],
    }
    assert validate(schema, normal) == []
    assert validate(schema, issue) == []
    assert validate(schema, {**normal, 'overall_status': 'fine'}) != []


def test_a_workspace_with_its_own_schema_of_that_name_keeps_it(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        own = conn.execute(
            "INSERT INTO ka.output_schemas(project_id,name,description) VALUES (%s,%s,'mine') RETURNING id",
            (project, NAME),
        ).fetchone()['id']
        run(conn, '0137_server_health_schema.up.sql')
        names = conn.execute(
            'SELECT id, description FROM ka.output_schemas WHERE project_id=%s AND name=%s', (project, NAME)
        ).fetchall()
    assert [(row['id'], row['description']) for row in names] == [(own, 'mine')]


def test_down_removes_only_the_untouched_seed(store):
    with store.connect() as conn:
        shop = store.project(conn, 'shop')
        edited = store.project(conn, 'edited')
        run(conn, '0137_server_health_schema.up.sql')
        schema_id = conn.execute(
            'SELECT id FROM ka.output_schemas WHERE project_id=%s AND name=%s', (edited, NAME)
        ).fetchone()['id']
        conn.execute(
            """INSERT INTO ka.output_schema_versions(schema_id,version,schema)
            SELECT schema_id, 2, schema FROM ka.output_schema_versions WHERE schema_id=%s""",
            (schema_id,),
        )
        conn.execute('UPDATE ka.output_schemas SET current_version=2 WHERE id=%s', (schema_id,))
        run(conn, '0137_server_health_schema.down.sql')
        assert seeded(conn, shop) == []
        assert len(seeded(conn, edited)) == 2
