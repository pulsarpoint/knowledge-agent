"""Tests that insert deployments by hand: every deployment is in an environment (0125)."""

from uuid import UUID


def environment_id(conn, project, name: str) -> UUID:
    """The project's environment of that name, created when missing."""
    row = conn.execute(
        'SELECT id FROM ka.environments WHERE project_id=%s AND lower(name)=lower(%s)', (project, name)
    ).fetchone()
    if row is not None:
        return row['id']
    return conn.execute(
        'INSERT INTO ka.environments(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
    ).fetchone()['id']
