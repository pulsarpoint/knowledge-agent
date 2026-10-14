"""knowledge_admin.db: the 503 for missing storage, a connection with its project, the audit row."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from knowledge_admin.db import audit, storage, workspace


def test_storage_returns_the_store_or_a_503_naming_what_is_missing():
    marker = object()
    assert storage(marker, 'Agent storage') is marker
    with pytest.raises(HTTPException) as error:
        storage(None, 'Agent storage')
    assert (error.value.status_code, error.value.detail) == (503, 'Agent storage is not configured.')
    with pytest.raises(HTTPException) as error:
        storage(None, 'Event storage', message='Event storage is not configured')
    assert (error.value.status_code, error.value.detail) == (503, 'Event storage is not configured')


def test_workspace_yields_the_connection_and_the_application_project(store):
    with workspace(store, 'shop') as (conn, project):
        assert project == store.project(conn, 'shop')
        assert conn.execute('SELECT name FROM ka.projects WHERE id=%s', (project,)).fetchone()['name'] == 'shop'


def test_workspace_rolls_back_when_the_block_fails(store):
    with pytest.raises(RuntimeError):
        with workspace(store, 'shop') as (conn, project):
            conn.execute("INSERT INTO ka.audit_events(project_id,action,resource_type) VALUES (%s,'x.test','x')", (project,))
            raise RuntimeError
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='x.test'").fetchone()['n'] == 0


def test_audit_writes_the_row_with_json_safe_details(store):
    resource, when = uuid4(), datetime(2026, 1, 2, tzinfo=timezone.utc)
    with workspace(store, 'shop') as (conn, project):
        actor = store.actor(conn, 'subject-1', 'person@example.test')
        audit(conn, project, actor, 'thing.saved', 'thing', resource, {'id': resource, 'at': when, 'n': 1})
        audit(conn, project, actor, 'thing.listed', 'thing', None, {})
    with store.connect() as conn:
        rows = conn.execute('''SELECT project_id,actor_id,action,resource_type,resource_id,details FROM ka.audit_events
            WHERE action LIKE 'thing.%%' ORDER BY id''').fetchall()
    assert rows == [
        {'project_id': project, 'actor_id': actor, 'action': 'thing.saved', 'resource_type': 'thing', 'resource_id': resource,
         'details': {'id': str(resource), 'at': '2026-01-02T00:00:00+00:00', 'n': 1}},
        {'project_id': project, 'actor_id': actor, 'action': 'thing.listed', 'resource_type': 'thing', 'resource_id': None,
         'details': {}},
    ]
