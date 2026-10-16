"""Migrations that introduced the per-server agent configurations (their endpoints are gone)."""

from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql


def test_migration_preserves_existing_defaults_and_server_runbooks(postgres):
    database = f'migration_{uuid4().hex}'
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}') as conn:
            for migration in sorted(migrations.glob('*.up.sql')):
                if migration.name < '0017':
                    conn.execute(migration.read_text(), prepare=False)
            project = conn.execute(
                "INSERT INTO ka.projects(slug,name) VALUES ('existing','Existing') RETURNING id"
            ).fetchone()[0]
            actor = conn.execute(
                "INSERT INTO ka.principals(issuer,subject,email) VALUES ('test','admin','admin@test') RETURNING id"
            ).fetchone()[0]
            server = conn.execute(
                "INSERT INTO ka.servers(project_id,name,hostname,operation_instructions,enabled) VALUES (%s,'Existing host','host.test','Existing runbook',false) RETURNING id",
                (project,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ka.operations_settings(project_id,instructions,revision) VALUES (%s,'Existing defaults',7)",
                (project,),
            )
            skill = conn.execute(
                "INSERT INTO ka.agent_skills(project_id,name,description,instructions,created_by,updated_by) VALUES (%s,'checks','Checks','Inspect first',%s,%s) RETURNING id",
                (project, actor, actor),
            ).fetchone()[0]
            conn.execute('INSERT INTO ka.operations_skills VALUES (%s,%s)', (project, skill))
            conn.execute((migrations / '0017_agent_server_configs.up.sql').read_text(), prepare=False)
            rows = conn.execute(
                'SELECT server_id,instructions,inherit_skills FROM ka.agent_configs ORDER BY server_id NULLS FIRST'
            ).fetchall()
            assert rows == [(None, 'Existing defaults', False), (server, 'Existing runbook', True)]
            assert conn.execute('SELECT skill_id FROM ka.agent_config_skills').fetchall() == [(skill,)]
            assert conn.execute('SELECT revision FROM ka.operations_settings').fetchone()[0] == 7
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def test_skills_agents_migration_preserves_shared_assignments_and_server_overrides(postgres):
    database = f'migration_{uuid4().hex}'
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}') as conn:
            for migration in sorted(migrations.glob('*.up.sql')):
                if migration.name < '0023':
                    conn.execute(migration.read_text(encoding='utf-8'), prepare=False)
            project = conn.execute(
                "INSERT INTO ka.projects(slug,name) VALUES ('existing','Existing') RETURNING id"
            ).fetchone()[0]
            actor = conn.execute(
                "INSERT INTO ka.principals(issuer,subject,email) VALUES ('test','admin','admin@test') RETURNING id"
            ).fetchone()[0]
            skill = conn.execute(
                "INSERT INTO ka.agent_skills(project_id,name,description,instructions,created_by,updated_by) VALUES (%s,'checks','Checks','Inspect first',%s,%s) RETURNING id",
                (project, actor, actor),
            ).fetchone()[0]
            server = conn.execute(
                "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'Existing host','host.test',false) RETURNING id",
                (project,),
            ).fetchone()[0]
            for agent in ('code', 'operations'):
                config = conn.execute(
                    "INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills) VALUES (%s,%s,'Agent instructions',false) RETURNING id",
                    (project, agent),
                ).fetchone()[0]
                conn.execute('INSERT INTO ka.agent_config_skills VALUES (%s,%s,%s)', (config, project, skill))
            override = conn.execute(
                "INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills) VALUES (%s,'operations',%s,'Server instructions',false) RETURNING id",
                (project, server),
            ).fetchone()[0]
            conn.execute('INSERT INTO ka.agent_config_skills VALUES (%s,%s,%s)', (override, project, skill))
            before = conn.execute('SELECT * FROM ka.agent_configs ORDER BY id').fetchall()
            conn.execute((migrations / '0023_skills_agents.up.sql').read_text(encoding='utf-8'), prepare=False)
            assert conn.execute(
                'SELECT agent_id,skill_id,project_id FROM ka.skills_agents ORDER BY agent_id'
            ).fetchall() == [('code', skill, project), ('operations', skill, project)]
            assert conn.execute('SELECT * FROM ka.agent_config_skills').fetchall() == [(override, project, skill)]
            assert conn.execute('SELECT * FROM ka.agent_configs ORDER BY id').fetchall() == before
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
