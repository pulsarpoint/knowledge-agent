"""Administer HTTP MCP servers, encrypted tokens, discovery, and code assignments."""
import asyncio
import os
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi import APIRouter, HTTPException, Request
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile
from knowledge_admin.access import request_actor
from knowledge_admin.mcp_settings import PUBLIC_COLUMNS, credential_aad, save_selection


class MCPInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    revision: int | None = Field(default=None, ge=1, strict=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=4000)
    url: str = Field(max_length=2000)
    enabled: bool = Field(default=True, strict=True)
    token: SecretStr | None = None

    @field_validator('url')
    @classmethod
    def endpoint(cls, value):
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query or any(c.isspace() for c in value):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError('Enter an HTTP or HTTPS MCP endpoint without credentials, query parameters, or fragments.') from None
        return value

    @field_validator('token')
    @classmethod
    def bearer(cls, value):
        if value is not None:
            secret = value.get_secret_value()
            if len(secret) > 16000 or any(c.isspace() for c in secret) or not secret.isascii():
                raise ValueError('Enter a bearer token without whitespace.')
        return value


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


class DeleteInput(RevisionInput):
    confirmed: Literal[True]


class SelectionInput(RevisionInput):
    mcp_server_ids: list[UUID] = Field(max_length=20)

    @field_validator('mcp_server_ids')
    @classmethod
    def distinct(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('Select each MCP server only once.')
        return value


async def discover(url, token):
    async with asyncio.timeout(25):
        async with httpx.AsyncClient(headers={'Authorization': f'Bearer {token}'} if token else {}, timeout=20, follow_redirects=False) as client:
            async with streamable_http_client(url, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools, cursor, seen = [], None, set()
                    for _ in range(20):
                        page = await session.list_tools(cursor=cursor)
                        tools.extend({'name': t.name, 'description': (t.description or '')[:4000]} for t in page.tools)
                        if len(tools) > 200 or any(not t['name'] or len(t['name']) > 128 for t in tools):
                            raise ValueError('MCP catalog exceeds limits')
                        cursor = page.nextCursor
                        if not cursor:
                            if len({t['name'] for t in tools}) != len(tools):
                                raise ValueError('Duplicate tool names')
                            return tools
                        if cursor in seen:
                            break
                        seen.add(cursor)
                    raise ValueError('MCP pagination did not finish')


def build_api(app_name, store):
    router = APIRouter(prefix='/mcp')

    def database():
        if store is None:
            raise HTTPException(503, 'MCP storage is not configured.')
        return store

    def audit(conn, request, action, entity, payload):
        actor = store.actor(conn, *request_actor(request))
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'mcp_server',%s,%s)''', (store.project(conn, app_name), actor, action, entity, Jsonb(payload)))
        return actor

    @router.get('/servers')
    def listing():
        db = database()
        with db.connect() as conn:
            return {'servers': conn.execute(f'SELECT {PUBLIC_COLUMNS} FROM ka.mcp_servers WHERE project_id=%s ORDER BY lower(name),id', (db.project(conn, app_name),)).fetchall()}

    def save(value, request, server_id):
        db = database()
        creating = server_id is None
        server_id = server_id or uuid4()
        try:
            with db.connect() as conn:
                project = db.project(conn, app_name)
                old = None if creating else conn.execute('SELECT * FROM ka.mcp_servers WHERE id=%s AND project_id=%s FOR UPDATE', (server_id, project)).fetchone()
                if not creating and old is None:
                    raise HTTPException(404, 'MCP server not found')
                if (None if old is None else old['revision']) != value.revision:
                    raise HTTPException(409, 'MCP server changed. Reopen it before saving.')
                encrypted = old['token_ciphertext'] if old else None
                replace_token = 'token' in value.model_fields_set and (value.token is None or value.token.get_secret_value() != '')
                if old and old['url'] != value.url and encrypted is not None and not replace_token:
                    raise HTTPException(400, 'Replace or remove the saved token when changing the endpoint.')
                if replace_token:
                    nonce = os.urandom(12)
                    encrypted = None if value.token is None else nonce + db.cipher.encrypt(nonce, value.token.get_secret_value().encode(), credential_aad(server_id))
                changed_connection = creating or old['url'] != value.url or replace_token
                actor = audit(conn, request, 'mcp.created' if creating else 'mcp.updated', server_id, {'name': value.name, 'enabled': value.enabled})
                if creating:
                    return conn.execute(f'''INSERT INTO ka.mcp_servers(id,project_id,name,description,url,enabled,token_ciphertext,updated_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {PUBLIC_COLUMNS}''', (server_id, project, value.name, value.description, value.url, value.enabled, encrypted, actor)).fetchone()
                return conn.execute(f'''UPDATE ka.mcp_servers SET name=%s,description=%s,url=%s,enabled=%s,token_ciphertext=%s,updated_by=%s,revision=revision+1,
                    tools=CASE WHEN %s THEN '[]'::jsonb ELSE tools END,
                    last_tested_at=CASE WHEN %s THEN NULL ELSE last_tested_at END,
                    last_test_error=CASE WHEN %s THEN NULL ELSE last_test_error END
                    WHERE id=%s RETURNING {PUBLIC_COLUMNS}''', (value.name, value.description, value.url, value.enabled, encrypted, actor, changed_connection, changed_connection, changed_connection, server_id)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'An MCP server with this name already exists.') from None

    @router.post('/servers', status_code=201)
    def create(value: MCPInput, request: Request):
        return save(value, request, None)

    @router.put('/servers/{server_id}')
    def update(server_id: UUID, value: MCPInput, request: Request):
        return save(value, request, server_id)

    @router.delete('/servers/{server_id}')
    def remove(server_id: UUID, value: DeleteInput, request: Request):
        db = database()
        with db.connect() as conn:
            row = conn.execute('SELECT revision FROM ka.mcp_servers WHERE id=%s AND project_id=%s FOR UPDATE', (server_id, db.project(conn, app_name))).fetchone()
            if row is None:
                raise HTTPException(404, 'MCP server not found')
            if row['revision'] != value.revision:
                raise HTTPException(409, 'MCP server changed. Refresh before removing it.')
            audit(conn, request, 'mcp.deleted', server_id, {})
            conn.execute('DELETE FROM ka.mcp_servers WHERE id=%s', (server_id,))
            return {'deleted': True}

    @router.post('/servers/{server_id}/test')
    async def test(server_id: UUID, value: RevisionInput, request: Request):
        db = database()
        with db.connect() as conn:
            row = conn.execute('SELECT * FROM ka.mcp_servers WHERE id=%s AND project_id=%s', (server_id, db.project(conn, app_name))).fetchone()
            if row is None:
                raise HTTPException(404, 'MCP server not found')
            if row['revision'] != value.revision:
                raise HTTPException(409, 'MCP server changed. Refresh before testing.')
            token = None
            if row['token_ciphertext'] is not None:
                try:
                    encrypted = bytes(row['token_ciphertext'])
                    token = db.cipher.decrypt(encrypted[:12], encrypted[12:], credential_aad(server_id)).decode()
                except Exception:
                    raise HTTPException(503, 'Could not decrypt the MCP token.') from None
        try:
            tools = await discover(row['url'], token)
            error = None
        except Exception:
            tools, error = [], 'Connection failed. Check the Streamable HTTP endpoint, token, and network access from Docker.'
        with db.connect() as conn:
            saved = conn.execute(f'''UPDATE ka.mcp_servers SET tools=%s,last_tested_at=now(),last_test_error=%s,revision=revision+1
                WHERE id=%s AND revision=%s RETURNING {PUBLIC_COLUMNS}''', (Jsonb(tools), error, server_id, value.revision)).fetchone()
            if saved is None:
                raise HTTPException(409, 'MCP server changed during the test. Test its current settings again.')
            audit(conn, request, 'mcp.tested', server_id, {'ok': error is None, 'tool_count': len(tools)})
            return {'ok': error is None, 'message': error or f'Connected. Discovered {len(tools)} tools.', 'server': saved}

    def code_config(conn, agent_id):
        try:
            profile(conn, agent_id)
        except ModelUnavailable:
            raise HTTPException(404, "Code agent not found") from None
        project = store.project(conn, app_name)
        conn.execute("INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills) VALUES (%s,%s,'',false) ON CONFLICT DO NOTHING", (project,agent_id))
        return conn.execute("SELECT id,project_id,revision FROM ka.agent_configs WHERE project_id=%s AND agent=%s AND server_id IS NULL FOR UPDATE", (project,agent_id)).fetchone()

    @router.get('/code')
    def selection(agent_id: str = "code"):
        db = database()
        with db.connect() as conn:
            config = code_config(conn, agent_id)
            return {'revision': config['revision'], 'mcp_server_ids': [r['mcp_server_id'] for r in conn.execute('SELECT mcp_server_id FROM ka.agent_config_mcp_servers WHERE config_id=%s ORDER BY mcp_server_id', (config['id'],))]}

    @router.put('/code')
    def assign(value: SelectionInput, request: Request, agent_id: str = "code"):
        db = database()
        with db.connect() as conn:
            config = code_config(conn, agent_id)
            if value.revision != config['revision']:
                raise HTTPException(409, 'Code agent MCP settings changed. Reload before saving.')
            try:
                save_selection(conn, config['project_id'], config['id'], value.mcp_server_ids)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            actor = audit(conn, request, 'mcp.code_assignment.updated', None, {'mcp_server_ids': [str(s) for s in value.mcp_server_ids]})
            conn.execute('UPDATE ka.agent_configs SET revision=revision+1,updated_by=%s WHERE id=%s', (actor, config['id']))
            return {'revision': config['revision'] + 1, 'mcp_server_ids': value.mcp_server_ids}

    return router
