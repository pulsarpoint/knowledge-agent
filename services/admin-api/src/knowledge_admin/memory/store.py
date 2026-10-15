"""Memory entries: one row per name in a project. Every change copies the current row to ka.memory_versions first and
records a memory.* event in the same transaction (the caller's connection; the caller commits). Callers embed the
content before calling — no lock is held during the model call — and pass the vector and its profile, or None."""

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from knowledge_llm import ModelUnavailable, embeddings
from psycopg.types.json import Jsonb

from knowledge_admin.memory.embedding import current_model

NAME = re.compile(r'[a-z0-9][a-z0-9._/-]{0,199}')
KEY = re.compile(r'[a-z][a-z0-9_]{0,63}')
MAX_KEYS, MAX_VALUE, MAX_CONTENT, BATCH = 20, 500, 20000, 32
NAME_RULE = 'A name is 1-200 characters of lowercase letters, digits and . _ / -, starting with a letter or digit.'
KEYS_RULE = 'Metadata has at most 20 keys.'
KEY_RULE = (
    'Metadata key "{key}" must be lowercase letters, digits and _, starting with a letter (at most 64 characters).'
)
VALUE_RULE = 'Metadata value of "{key}" must be text of 1-500 characters.'
CONTENT_RULE = 'Content must be 1-20,000 characters and not only spaces.'
NUL_RULE = 'Text must not contain NUL characters.'

ENTRY = """SELECT m.id,m.name,m.metadata,m.content,m.revision,m.created_at,m.updated_at,
    m.created_by_agent,ca.name AS created_by_agent_name,m.updated_by_agent,ua.name AS updated_by_agent_name,
    m.updated_by_actor,p.email AS updated_by_email,m.embedding_profile
    FROM ka.memories m LEFT JOIN ka.agents ca ON ca.id=m.created_by_agent
    LEFT JOIN ka.agents ua ON ua.id=m.updated_by_agent LEFT JOIN ka.principals p ON p.id=m.updated_by_actor"""


class Invalid(ValueError):
    pass


class Exists(Exception):
    def __init__(self, entry: dict):
        super().__init__(entry['name'])
        self.entry = entry


class Stale(Exception):
    def __init__(self, entry: dict):
        super().__init__(entry['name'])
        self.entry = entry


class Missing(Exception):
    pass


@dataclass(frozen=True)
class Author:
    agent_id: str | None = None
    session_id: UUID | None = None
    actor_id: UUID | None = None


def validate(name: str, metadata: Any, content: str) -> None:
    if not isinstance(name, str) or NAME.fullmatch(name) is None:
        raise Invalid(NAME_RULE)
    if not isinstance(metadata, dict):
        raise Invalid(KEYS_RULE)
    if len(metadata) > MAX_KEYS:
        raise Invalid(KEYS_RULE)
    for key, value in metadata.items():
        if not isinstance(key, str) or KEY.fullmatch(key) is None:
            raise Invalid(KEY_RULE.format(key=key))
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_VALUE:
            raise Invalid(VALUE_RULE.format(key=key))
        if '\x00' in value:
            raise Invalid(NUL_RULE)
    if not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT:
        raise Invalid(CONTENT_RULE)
    if '\x00' in content:
        raise Invalid(NUL_RULE)


def get(conn, project_id: UUID, *, name: str | None = None, memory_id: UUID | None = None) -> dict | None:
    column, value = ('m.name', name) if name is not None else ('m.id', memory_id)
    return conn.execute(f'{ENTRY} WHERE m.project_id=%s AND {column}=%s', (project_id, value)).fetchone()


def versions(conn, memory_id: UUID) -> list[dict]:
    return conn.execute(
        """SELECT v.revision,v.name,v.metadata,v.content,v.by_agent,a.name AS by_agent_name,v.by_actor,
        p.email AS by_email,v.changed_at FROM ka.memory_versions v LEFT JOIN ka.agents a ON a.id=v.by_agent
        LEFT JOIN ka.principals p ON p.id=v.by_actor WHERE v.memory_id=%s ORDER BY v.revision DESC""",
        (memory_id,),
    ).fetchall()


def version(conn, memory_id: UUID, revision: int) -> dict | None:
    return conn.execute(
        'SELECT revision,name,metadata,content FROM ka.memory_versions WHERE memory_id=%s AND revision=%s',
        (memory_id, revision),
    ).fetchone()


def _event(
    conn, project_id: UUID, kind: str, memory_id: UUID, name: str, metadata: dict, revision: int, author: Author
) -> None:
    conn.execute(
        """INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,occurred_at)
        VALUES (%s,'memory',%s,%s,%s,'',%s,%s,now())""",
        (
            project_id,
            memory_id,
            kind,
            name,
            Jsonb(
                {
                    'memory_id': str(memory_id),
                    'name': name,
                    'metadata': metadata,
                    'revision': revision,
                    'agent_id': author.agent_id,
                    'session_id': None if author.session_id is None else str(author.session_id),
                    'actor': None if author.actor_id is None else str(author.actor_id),
                }
            ),
            f'memory:{memory_id}:{revision}:{kind}',
        ),
    )


def _vector(vector: list[float] | None) -> str | None:
    return None if vector is None else json.dumps(vector)


def create(
    conn,
    project_id: UUID,
    name: str,
    metadata: dict,
    content: str,
    author: Author,
    vector: list[float] | None,
    profile: str | None,
) -> dict:
    validate(name, metadata, content)
    try:
        with conn.transaction():
            memory_id = conn.execute(
                """INSERT INTO ka.memories(project_id,name,metadata,content,embedding,embedding_profile,
                created_by_agent,updated_by_agent,created_by_session,updated_by_session,created_by_actor,updated_by_actor)
                VALUES (%s,%s,%s,%s,%s::public.vector,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    project_id,
                    name,
                    Jsonb(metadata),
                    content,
                    _vector(vector),
                    profile if vector else None,
                    author.agent_id,
                    author.agent_id,
                    author.session_id,
                    author.session_id,
                    author.actor_id,
                    author.actor_id,
                ),
            ).fetchone()['id']
    except psycopg.errors.UniqueViolation:
        raise Exists(get(conn, project_id, name=name)) from None
    _event(conn, project_id, 'memory.saved', memory_id, name, metadata, 1, author)
    return get(conn, project_id, memory_id=memory_id)


def _locked(conn, project_id: UUID, memory_id: UUID) -> dict:
    row = conn.execute(
        'SELECT * FROM ka.memories WHERE id=%s AND project_id=%s FOR UPDATE', (memory_id, project_id)
    ).fetchone()
    if row is None:
        raise Missing()
    return row


def _keep(conn, row: dict) -> None:
    conn.execute(
        """INSERT INTO ka.memory_versions(memory_id,revision,name,metadata,content,by_agent,by_session,by_actor,changed_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            row['id'],
            row['revision'],
            row['name'],
            Jsonb(row['metadata']),
            row['content'],
            row['updated_by_agent'],
            row['updated_by_session'],
            row['updated_by_actor'],
            row['updated_at'],
        ),
    )


def _replace(
    conn,
    project_id: UUID,
    row: dict,
    kind: str,
    name: str,
    metadata: dict,
    content: str,
    author: Author,
    vector: list[float] | None,
    profile: str | None,
) -> dict:
    validate(name, metadata, content)
    try:
        with conn.transaction():
            _keep(conn, row)
            conn.execute(
                """UPDATE ka.memories SET name=%s,metadata=%s,content=%s,embedding=%s::public.vector,
                embedding_profile=%s,revision=revision+1,updated_by_agent=%s,updated_by_session=%s,
                updated_by_actor=%s,updated_at=now() WHERE id=%s""",
                (
                    name,
                    Jsonb(metadata),
                    content,
                    _vector(vector),
                    profile if vector else None,
                    author.agent_id,
                    author.session_id,
                    author.actor_id,
                    row['id'],
                ),
            )
    except psycopg.errors.UniqueViolation:
        raise Exists(get(conn, project_id, name=name)) from None
    _event(conn, project_id, kind, row['id'], name, metadata, row['revision'] + 1, author)
    return get(conn, project_id, memory_id=row['id'])


def update(
    conn,
    project_id: UUID,
    memory_id: UUID,
    name: str,
    metadata: dict,
    content: str,
    revision: int,
    author: Author,
    vector: list[float] | None,
    profile: str | None,
) -> dict:
    row = _locked(conn, project_id, memory_id)
    if row['revision'] != revision:
        raise Stale(get(conn, project_id, memory_id=memory_id))
    return _replace(conn, project_id, row, 'memory.updated', name, metadata, content, author, vector, profile)


def restore(
    conn,
    project_id: UUID,
    memory_id: UUID,
    revision: int,
    current_revision: int,
    author: Author,
    vector: list[float] | None,
    profile: str | None,
) -> dict:
    row = _locked(conn, project_id, memory_id)
    if row['revision'] != current_revision:
        raise Stale(get(conn, project_id, memory_id=memory_id))
    old = version(conn, memory_id, revision)
    if old is None:
        raise Missing()
    return _replace(
        conn, project_id, row, 'memory.restored', old['name'], old['metadata'], old['content'], author, vector, profile
    )


def delete(conn, project_id: UUID, memory_id: UUID, revision: int, author: Author) -> None:
    row = _locked(conn, project_id, memory_id)
    if row['revision'] != revision:
        raise Stale(get(conn, project_id, memory_id=memory_id))
    conn.execute('DELETE FROM ka.memories WHERE id=%s', (memory_id,))
    _event(conn, project_id, 'memory.deleted', memory_id, row['name'], row['metadata'], row['revision'], author)


def reembed(store, project_id: UUID, transport=None) -> dict:
    """Embed, in batches of 32, every entry without a vector of the current model's profile. Not a change of the entry:
    no new revision, no event; an entry edited meanwhile (revision moved) keeps its own new vector. Raises Missing when
    there is no usable model."""
    with store.connect() as conn:
        model = current_model(conn, project_id)
    if model is None:
        raise Missing()
    profile = embeddings.embedding_profile(model)
    embedded = failed = 0
    tried: list[UUID] = []
    while True:
        with store.connect() as conn:
            rows = conn.execute(
                """SELECT id,content,revision FROM ka.memories WHERE project_id=%s
                AND embedding_profile IS DISTINCT FROM %s AND NOT (id = ANY(%s)) ORDER BY id LIMIT %s""",
                (project_id, profile, tried, BATCH),
            ).fetchall()
        if not rows:
            return {'embedded': embedded, 'failed': failed}
        tried += [row['id'] for row in rows]
        try:
            vectors = embeddings.embed(model, [row['content'] for row in rows], transport)
        except ModelUnavailable:
            failed += len(rows)
            continue
        with store.connect() as conn:
            for row, vector in zip(rows, vectors, strict=True):
                done = conn.execute(
                    """UPDATE ka.memories SET embedding=%s::public.vector,embedding_profile=%s
                    WHERE id=%s AND revision=%s""",
                    (json.dumps(vector), profile, row['id'], row['revision']),
                ).rowcount
                embedded += done
