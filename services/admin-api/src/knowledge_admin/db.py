"""Small helpers for the plain-SQL routers: the 503 when storage is missing, a connection with its project, the audit row."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

if TYPE_CHECKING:  # repositories.py uses these helpers too
    from knowledge_admin.repositories import RepositoryStore


def storage(store: 'RepositoryStore | None', what: str, *, message: str | None = None) -> 'RepositoryStore':
    """The store, or a 503 saying `what` is not configured (`message` replaces the whole text)."""
    if store is None:
        raise HTTPException(503, message if message is not None else f'{what} is not configured.')
    return store


@contextmanager
def workspace(store: 'RepositoryStore', app_name: str) -> Iterator[tuple[object, UUID]]:
    """One connection (one transaction) and the application's project, resolved first thing."""
    with store.connect() as conn:
        yield conn, store.project(conn, app_name)


def audit(conn, project, actor, action: str, resource_type: str, resource_id, details: dict) -> None:
    conn.execute(
        """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
        VALUES (%s,%s,%s,%s,%s,%s)""",
        (project, actor, action, resource_type, resource_id, Jsonb(jsonable_encoder(details))),
    )
