"""Output schemas: one row per name in a project and one version row per saved shape. A save of a changed schema adds
version current_version + 1; saving anything else only bumps the revision. Functions use the caller's connection; the
caller commits."""

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = """SELECT s.id,s.name,s.description,s.current_version,s.revision,s.created_at,s.updated_at,
    COALESCE((SELECT array_agg(h.name ORDER BY h.name) FROM ka.event_handlers h WHERE h.output_schema_id=s.id),
        '{}') AS used_by
    FROM ka.output_schemas s"""
CURRENT = """SELECT v.schema FROM ka.output_schema_versions v WHERE v.schema_id=%s AND v.version=%s"""


class Exists(Exception):
    pass


class Missing(Exception):
    pass


class Stale(Exception):
    pass


class InUse(Exception):
    def __init__(self, names: list[str]):
        super().__init__(', '.join(names))


def list_all(conn, project: UUID) -> list[dict]:
    return conn.execute(f'{SCHEMA} WHERE s.project_id=%s ORDER BY s.name,s.id', (project,)).fetchall()


def get(conn, project: UUID, schema_id: UUID) -> dict | None:
    """The schema with its current JSON and its versions, newest first."""
    row = conn.execute(f'{SCHEMA} WHERE s.project_id=%s AND s.id=%s', (project, schema_id)).fetchone()
    if row is None:
        return None
    row['schema'] = conn.execute(CURRENT, (schema_id, row['current_version'])).fetchone()['schema']
    row['versions'] = conn.execute(
        """SELECT v.version,v.created_at,p.email AS created_by_email FROM ka.output_schema_versions v
        LEFT JOIN ka.principals p ON p.id=v.created_by WHERE v.schema_id=%s ORDER BY v.version DESC""",
        (schema_id,),
    ).fetchall()
    return row


def version(conn, project: UUID, schema_id: UUID, number: int) -> dict | None:
    return conn.execute(
        """SELECT v.version,v.schema,v.created_at FROM ka.output_schema_versions v JOIN ka.output_schemas s
        ON s.id=v.schema_id WHERE s.project_id=%s AND v.schema_id=%s AND v.version=%s""",
        (project, schema_id, number),
    ).fetchone()


def create(conn, project: UUID, name: str, description: str, schema: dict, actor: UUID) -> dict:
    try:
        with conn.transaction():
            schema_id = conn.execute(
                'INSERT INTO ka.output_schemas(project_id,name,description) VALUES (%s,%s,%s) RETURNING id',
                (project, name, description),
            ).fetchone()['id']
    except psycopg.errors.UniqueViolation:
        raise Exists from None
    conn.execute(
        'INSERT INTO ka.output_schema_versions(schema_id,version,schema,created_by) VALUES (%s,1,%s,%s)',
        (schema_id, Jsonb(schema), actor),
    )
    return get(conn, project, schema_id)


def update(
    conn, project: UUID, schema_id: UUID, name: str, description: str, schema: dict, revision: int, actor: UUID
) -> dict:
    row = _locked(conn, project, schema_id, revision)
    number = row['current_version']
    if conn.execute(CURRENT, (schema_id, number)).fetchone()['schema'] != schema:
        number += 1
        conn.execute(
            'INSERT INTO ka.output_schema_versions(schema_id,version,schema,created_by) VALUES (%s,%s,%s,%s)',
            (schema_id, number, Jsonb(schema), actor),
        )
    try:
        with conn.transaction():
            conn.execute(
                """UPDATE ka.output_schemas SET name=%s,description=%s,current_version=%s,revision=revision+1,
                updated_at=now() WHERE id=%s""",
                (name, description, number, schema_id),
            )
    except psycopg.errors.UniqueViolation:
        raise Exists from None
    return get(conn, project, schema_id)


def delete(conn, project: UUID, schema_id: UUID, revision: int) -> None:
    """Delete a schema and its versions, unless a handler uses it."""
    row = _locked(conn, project, schema_id, revision)
    if row['used_by']:
        raise InUse(row['used_by'])
    conn.execute('DELETE FROM ka.output_schemas WHERE id=%s', (schema_id,))


def _locked(conn, project: UUID, schema_id: UUID, revision: int) -> dict:
    conn.execute('SELECT 1 FROM ka.output_schemas WHERE project_id=%s AND id=%s FOR UPDATE', (project, schema_id))
    row = conn.execute(f'{SCHEMA} WHERE s.project_id=%s AND s.id=%s', (project, schema_id)).fetchone()
    if row is None:
        raise Missing
    if row['revision'] != revision:
        raise Stale
    return row
