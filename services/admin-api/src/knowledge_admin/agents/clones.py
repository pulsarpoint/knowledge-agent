"""Clone an agent; apply a clone's configuration back to its original. An agent has one configuration (0080)."""

from functools import partial
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.identity import request_actor
from knowledge_admin.db import audit as write_audit, storage
from knowledge_admin.knowledge_agents.config import changed_sections, fingerprint, saved_config
from knowledge_admin.agents.profiles_api import WORKING

NAME_TAKEN = 'An agent with this name already exists.'
NAME_LIMIT = 100
NOT_A_CLONE = 'This agent is not a clone.'
APPLY_WORKING = (
    'The original agent is working right now. Wait for its documentation sessions and runs to finish, then apply.'
)
ORIGINAL_CHANGED = 'The original changed; review again.'
# Everything an agent runs with on ka.agents. Name, description, enabled and cloned_from belong to the agent itself.
CONFIGURATION = ('when_to_use', 'model_id', 'system_prompt', 'documents_grounded', 'access', 'instructions')


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)


def clone_name(conn, name: str) -> str:
    """ "<name> (copy)", then "(copy 2)", "(copy 3)" … while taken; shortened to the 100-character name limit."""
    taken = {row['name'] for row in conn.execute('SELECT name FROM ka.agents')}
    number = 1
    while True:
        suffix = ' (copy)' if number == 1 else f' (copy {number})'
        candidate = name[: NAME_LIMIT - len(suffix)].rstrip() + suffix
        if candidate not in taken:
            return candidate
        number += 1


def copy_configuration(conn, source_id: str, target_id: str, actor_id) -> None:
    """Make the target run exactly what the source runs: every configuration column (its access object and
    instructions among them). Bumps the target's revision."""
    columns = ','.join(f'{column}=s.{column}' for column in CONFIGURATION)
    conn.execute(
        f"""UPDATE ka.agents t SET {columns},revision=t.revision+1,updated_by=%s
        FROM ka.agents s WHERE s.id=%s AND t.id=%s""",
        (actor_id, source_id, target_id),
    )


def clone(conn, source_id: str, actor_id) -> dict[str, str]:
    """A new agent with the source's configuration and on/off switch, named after it. History is not copied."""
    source = conn.execute(
        'SELECT name,description,enabled,system_prompt FROM ka.agents WHERE id=%s FOR UPDATE', (source_id,)
    ).fetchone()
    if source is None:
        raise LookupError('Agent not found')
    created = {'id': str(uuid4()), 'name': clone_name(conn, source['name'])}
    conn.execute(
        """INSERT INTO ka.agents(id,name,description,enabled,system_prompt,cloned_from,created_by,updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            created['id'],
            created['name'],
            source['description'],
            source['enabled'],
            source['system_prompt'],
            source_id,
            actor_id,
            actor_id,
        ),
    )
    copy_configuration(conn, source_id, created['id'], actor_id)
    return created


def build_api(app_name, store):
    router = APIRouter(prefix='/agents/{agent_id}')

    database = partial(storage, store, 'Agent storage')

    def audit(conn, actor, action: str, details: dict) -> None:
        write_audit(conn, store.project(conn, app_name), actor, action, 'agent', None, details)

    @router.post('/clone', status_code=201)
    def clone_agent(agent_id: str, request: Request):
        try:
            with database().connect() as conn:
                actor = store.actor(conn, *request_actor(request))
                try:
                    created = clone(conn, agent_id, actor)
                except LookupError:
                    raise HTTPException(404, 'Agent not found') from None
                audit(conn, actor, 'agent.cloned', {'agent_id': created['id'], 'cloned_from': agent_id})
                return created
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, NAME_TAKEN) from None

    def config(conn, agent_id: str) -> dict:
        try:
            return saved_config(conn, agent_id)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None

    def original_of(conn, agent_id: str) -> dict:
        row = conn.execute(
            """SELECT o.id,o.name,o.revision FROM ka.agents c LEFT JOIN ka.agents o ON o.id=c.cloned_from
            WHERE c.id=%s""",
            (agent_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, 'Agent not found')
        if row['id'] is None:  # never a clone, or its original was deleted
            raise HTTPException(404, NOT_A_CLONE)
        return row

    @router.get('/apply')
    def preview(agent_id: str, response: Response):
        """What Apply would replace on the original, and the fingerprint the dialog sends back."""
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            original = original_of(conn, agent_id)
            target = config(conn, original['id'])
            return {
                'original': original,
                'fingerprint': fingerprint(target),
                'changes': changed_sections(config(conn, agent_id), target),
            }

    @router.post('/apply')
    def apply(agent_id: str, value: ApplyInput, request: Request):
        """Copy the clone's configuration onto its original in one transaction (not its name, description, on/off
        switch or cloned_from)."""
        with database().connect() as conn:
            original = original_of(conn, agent_id)
            for row_id in sorted((agent_id, original['id'])):
                conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (row_id,))
            original = original_of(conn, agent_id)  # again, under the locks
            if conn.execute(WORKING, {'a': original['id']}).fetchone():
                raise HTTPException(409, APPLY_WORKING)
            # The fingerprint is read after the locks: writers of the original wait on them.
            if original['revision'] != value.revision or fingerprint(config(conn, original['id'])) != value.fingerprint:
                raise HTTPException(409, ORIGINAL_CHANGED)
            actor = store.actor(conn, *request_actor(request))
            copy_configuration(conn, agent_id, original['id'], actor)
            audit(conn, actor, 'agent.config.applied', {'agent_id': original['id'], 'clone_id': agent_id})
            return conn.execute('SELECT id,revision FROM ka.agents WHERE id=%s', (original['id'],)).fetchone()

    return router
