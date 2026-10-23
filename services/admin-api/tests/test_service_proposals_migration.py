"""0129: service proposals are gone (inventory findings are drafts); down restores the shape, not the data."""

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_discovery_runs import config_of, ssh_ready, start
from discovery_world import world  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0129_service_proposals_gone.up.sql', '0129_service_proposals_gone.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='discovery_proposals'"
    )
    return {row['column_name'] for row in rows}


def test_only_document_proposals_remain_and_the_link_column_is_gone(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    with world.store.connect() as conn:
        assert 'service_proposal_id' not in columns(conn)
        add = """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload)
            VALUES (%s,%s,%s,1,%s,%s)"""
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(add, (world.project, sid, world.server['id'], 'service', Jsonb({'name': 'redis'})))
        conn.execute(add, (world.project, sid, world.server['id'], 'document', Jsonb({'title': 'x'})))


def test_down_restores_service_proposals_and_up_deletes_them_with_the_documents_about_them(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    with world.store.connect() as conn:
        run(conn, DOWN)
        assert 'service_proposal_id' in columns(conn)
        add = """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload,service_proposal_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id"""
        args = (world.project, sid, world.server['id'])
        service = conn.execute(add, (*args, 1, 'service', Jsonb({'name': 'redis'}), None)).fetchone()['id']
        conn.execute(add, (*args, 2, 'document', Jsonb({'title': 'Redis'}), service))
        conn.execute(add, (*args, 3, 'document', Jsonb({'title': 'Disks'}), None))
        run(conn, UP)
        rows = conn.execute('SELECT number,kind FROM ka.discovery_proposals ORDER BY number').fetchall()
        assert [(row['number'], row['kind']) for row in rows] == [(3, 'document')]
        assert 'service_proposal_id' not in columns(conn)
