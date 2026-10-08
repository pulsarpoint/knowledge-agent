"""Server connection secrets and the operations agent's administrator-owned policy."""
import os
from typing import Literal
from uuid import UUID, uuid4

import asyncssh
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from knowledge_admin.repositories import RepositoryStore
from knowledge_llm.profiles import profile
from knowledge_llm import ModelUnavailable
from knowledge_admin.mcp_settings import save_selection
from knowledge_admin.capabilities.ssh import SSHConnection


class SSHInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    username: str | None = Field(default=None, pattern=r'^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}[$]?$', max_length=65)
    port: int = Field(default=22, ge=1, le=65535, strict=True)
    allow_sudo: bool = Field(default=False, strict=True)
    known_hosts: str = Field(default='', max_length=40000)
    private_key: SecretStr | None = None

    @field_validator('private_key')
    @classmethod
    def check_key(cls, value):
        if value is not None:
            secret = value.get_secret_value()
            if len(secret) > 40000:
                raise ValueError('Private key is too large')
            try:
                asyncssh.import_private_key(secret)
            except (ValueError, asyncssh.Error):
                raise ValueError('Enter a valid private key without a passphrase. It will be encrypted in storage.') from None
        return value

    @field_validator('known_hosts')
    @classmethod
    def check_hosts(cls, value):
        if value.strip():
            try:
                asyncssh.import_known_hosts(value)
            except (ValueError, asyncssh.Error):
                raise ValueError('Enter trusted OpenSSH known_hosts entries.') from None
        return value


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    instructions: str = Field(max_length=40000)
    server_ids: list[UUID] = Field(max_length=100)
    skill_ids: list[UUID] = Field(max_length=30)
    mcp_server_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @field_validator('server_ids', 'skill_ids', 'mcp_server_ids')
    @classmethod
    def distinct_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Selections must be unique')
        return value


class AgentSSHInput(SSHInput):
    revision: int = Field(ge=0, strict=True)
    server_revision: int = Field(ge=1, strict=True)


class DeleteAgentSSHInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    server_revision: int = Field(ge=1, strict=True)
    confirmed: Literal[True]


class ServerConfigInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    settings_revision: int = Field(ge=1, strict=True)
    revision: int | None = Field(ge=1, strict=True)
    instructions: str | None = Field(max_length=40000)
    skill_ids: list[UUID] | None = Field(max_length=30)
    mcp_server_ids: list[UUID] | None = Field(default=None, max_length=20)

    @field_validator('skill_ids', 'mcp_server_ids')
    @classmethod
    def distinct_skills(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError('Selections must be unique')
        return value


class DeleteServerConfigInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    settings_revision: int = Field(ge=1, strict=True)
    revision: int = Field(ge=1, strict=True)
    confirmed: Literal[True]


class OperationsStore:
    def __init__(self, store: RepositoryStore, app_name: str, agent_id: str = "operations"):
        self.db = store
        self.app_name = app_name
        self.agent_id = agent_id

    def project(self, conn):
        try:
            profile(conn, self.agent_id)
        except ModelUnavailable:
            raise HTTPException(404, 'Agent not found') from None
        return self.db.project(conn, self.app_name)

    def settings(self, conn, *, lock: bool = False):
        project = self.project(conn)
        conn.execute('INSERT INTO ka.operations_settings(project_id,agent) VALUES (%s,%s) ON CONFLICT DO NOTHING', (project,self.agent_id))
        settings = conn.execute('SELECT * FROM ka.operations_settings WHERE project_id=%s AND agent=%s' + (' FOR UPDATE' if lock else ''), (project,self.agent_id)).fetchone()
        conn.execute("""INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills)
            VALUES (%s,%s,'',false) ON CONFLICT DO NOTHING""", (project,self.agent_id))
        global_config = next(c for c in self.configs(conn) if c['server_id'] is None)
        settings['instructions'] = global_config['instructions']
        settings['skill_ids'] = global_config['skill_ids']
        settings['mcp_server_ids'] = global_config['mcp_server_ids']
        settings['server_ids'] = [r['server_id'] for r in conn.execute('SELECT server_id FROM ka.operations_servers WHERE project_id=%s AND agent=%s ORDER BY server_id', (project,self.agent_id))]
        return settings

    def configs(self, conn):
        return conn.execute("""SELECT c.id,c.server_id,c.instructions,c.revision,
            CASE WHEN c.server_id IS NULL THEN ARRAY(
                SELECT s.skill_id FROM ka.skills_agents s WHERE s.agent_id=c.agent AND s.project_id=c.project_id ORDER BY s.skill_id
            ) WHEN c.inherit_skills THEN NULL ELSE ARRAY(
                SELECT s.skill_id FROM ka.agent_config_skills s WHERE s.config_id=c.id ORDER BY s.skill_id
            ) END AS skill_ids,
            CASE WHEN c.inherit_mcp_servers THEN NULL ELSE ARRAY(
                SELECT m.mcp_server_id FROM ka.agent_config_mcp_servers m WHERE m.config_id=c.id ORDER BY m.mcp_server_id
            ) END AS mcp_server_ids
            FROM ka.agent_configs c WHERE c.project_id=%s AND c.agent=%s
            ORDER BY c.server_id NULLS FIRST""", (self.project(conn),self.agent_id)).fetchall()

    def servers(self, conn):
        return conn.execute('''SELECT id,name,hostname,port,username,description,tags,ssh_allowed,enabled,allow_sudo,revision,
            coalesce(known_hosts,'') AS known_hosts,(credential_id IS NOT NULL) AS has_private_key,last_tested_at,last_test_error
            FROM ka.servers WHERE project_id=%s ORDER BY lower(name),id''', (self.project(conn),)).fetchall()

    def server(self, conn, server_id):
        row = next((s for s in self.servers(conn) if s['id'] == server_id), None)
        if row is None:
            raise HTTPException(404, 'Server not found')
        return row

    def agent_servers(self, conn):
        return conn.execute('''SELECT s.id,s.name,s.hostname,s.description,s.tags,s.ssh_allowed,s.revision AS server_revision,
            coalesce(a.revision,0) AS revision,
            CASE WHEN a.server_id IS NULL THEN 'server' ELSE 'agent' END AS connection_source,
            CASE WHEN a.server_id IS NULL THEN s.username ELSE a.username END AS username,
            coalesce(a.port,s.port) AS port,coalesce(a.allow_sudo,s.allow_sudo) AS allow_sudo,
            coalesce(a.known_hosts,s.known_hosts,'') AS known_hosts,
            CASE WHEN a.server_id IS NULL THEN s.credential_id IS NOT NULL ELSE a.credential_id IS NOT NULL END AS has_private_key,
            CASE WHEN a.server_id IS NULL THEN s.enabled ELSE a.credential_id IS NOT NULL AND a.username IS NOT NULL END AS enabled,
            CASE WHEN a.server_id IS NULL THEN s.last_tested_at ELSE a.last_tested_at END AS last_tested_at,
            CASE WHEN a.server_id IS NULL THEN s.last_test_error ELSE a.last_test_error END AS last_test_error
            FROM ka.servers s LEFT JOIN ka.agent_server_connections a ON a.server_id=s.id AND a.agent_id=%s
            WHERE s.project_id=%s ORDER BY lower(s.name),s.id''', (self.agent_id,self.project(conn))).fetchall()

    def agent_server(self, conn, server_id):
        row = next((s for s in self.agent_servers(conn) if s['id'] == server_id), None)
        if row is None:
            raise HTTPException(404, 'Server not found')
        return row

    def save_agent_ssh(self, server_id: UUID, value: AgentSSHInput, identity):
        with self.db.connect() as conn:
            project = self.settings(conn, lock=True)['project_id']
            conn.execute('SELECT id FROM ka.servers WHERE id=%s AND project_id=%s FOR UPDATE', (server_id,project))
            current = self.agent_server(conn, server_id)
            if current['revision'] != value.revision or current['server_revision'] != value.server_revision:
                raise HTTPException(409, 'SSH settings or server changed. Close and reopen before saving.')
            actor = self.db.actor(conn, *identity)
            saved = conn.execute('SELECT credential_id FROM ka.agent_server_connections WHERE agent_id=%s AND server_id=%s', (self.agent_id,server_id)).fetchone()
            old_credential = saved['credential_id'] if saved is not None else conn.execute('SELECT credential_id FROM ka.servers WHERE id=%s', (server_id,)).fetchone()['credential_id']
            credential = old_credential
            if value.private_key is not None:
                credential = uuid4()
                nonce = os.urandom(12)
                ciphertext = nonce + self.db.cipher.encrypt(nonce, value.private_key.get_secret_value().encode(), f'{project}:{credential}:ssh_private_key:v1'.encode())
                conn.execute('''INSERT INTO ka.credentials(id,project_id,name,kind,ciphertext,key_version,created_by)
                    VALUES (%s,%s,%s,'ssh_private_key',%s,'v1',%s)''',
                    (credential,project,f'agent-ssh-{credential}',ciphertext,actor))
            elif 'private_key' in value.model_fields_set:
                credential = None
            conn.execute('''INSERT INTO ka.agent_server_connections(agent_id,server_id,project_id,username,port,allow_sudo,known_hosts,credential_id,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(agent_id,server_id) DO UPDATE SET
                username=EXCLUDED.username,port=EXCLUDED.port,allow_sudo=EXCLUDED.allow_sudo,known_hosts=EXCLUDED.known_hosts,
                credential_id=EXCLUDED.credential_id,updated_by=EXCLUDED.updated_by,revision=agent_server_connections.revision+1,
                last_tested_at=NULL,last_test_error=NULL''',
                (self.agent_id,server_id,project,value.username,value.port,value.allow_sudo,value.known_hosts,credential,actor))
            if old_credential is not None and old_credential != credential:
                conn.execute('''DELETE FROM ka.credentials WHERE id=%s
                    AND NOT EXISTS (SELECT 1 FROM ka.servers WHERE credential_id=%s)
                    AND NOT EXISTS (SELECT 1 FROM ka.agent_server_connections WHERE credential_id=%s)''',
                    (old_credential,old_credential,old_credential))
            self.audit(conn, actor, 'agent.ssh.updated', server_id, {'agent_id': self.agent_id,
                'has_private_key': credential is not None, 'allow_sudo': value.allow_sudo})
            conn.execute('UPDATE ka.operations_settings SET revision=revision+1,updated_by=%s WHERE project_id=%s AND agent=%s', (actor,project,self.agent_id))
            return self.agent_server(conn, server_id)

    def delete_agent_ssh(self, server_id: UUID, value: DeleteAgentSSHInput, identity):
        with self.db.connect() as conn:
            project = self.settings(conn, lock=True)['project_id']
            conn.execute('SELECT id FROM ka.servers WHERE id=%s AND project_id=%s FOR UPDATE', (server_id,project))
            current = self.agent_server(conn, server_id)
            if current['revision'] != value.revision or current['server_revision'] != value.server_revision:
                raise HTTPException(409, 'SSH settings or server changed. Close and reopen before removing the override.')
            row = conn.execute('DELETE FROM ka.agent_server_connections WHERE agent_id=%s AND server_id=%s RETURNING credential_id', (self.agent_id,server_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'SSH override not found')
            credential = row['credential_id']
            if credential is not None:
                conn.execute('''DELETE FROM ka.credentials WHERE id=%s
                    AND NOT EXISTS (SELECT 1 FROM ka.servers WHERE credential_id=%s)
                    AND NOT EXISTS (SELECT 1 FROM ka.agent_server_connections WHERE credential_id=%s)''', (credential,credential,credential))
            self.audit(conn, self.db.actor(conn, *identity), 'agent.ssh.removed', server_id, {'agent_id': self.agent_id})
            conn.execute('UPDATE ka.operations_settings SET revision=revision+1 WHERE project_id=%s AND agent=%s', (project,self.agent_id))
            return self.agent_server(conn, server_id)

    def agent_connection(self, conn, server_id: UUID, *, test_settings: AgentSSHInput | None = None) -> SSHConnection:
        row = self.agent_server(conn, server_id)
        if not row['ssh_allowed']:
            raise HTTPException(409, 'SSH is disabled for this server.')
        if test_settings is not None:
            if row['revision'] != test_settings.revision or row['server_revision'] != test_settings.server_revision:
                raise HTTPException(409, 'SSH settings or server changed. Reload before testing.')
            for name in ('username','port','allow_sudo','known_hosts'):
                if name in test_settings.model_fields_set:
                    row[name] = getattr(test_settings, name)
        if row['username'] is None:
            raise HTTPException(409, 'Set an SSH login user for this agent before connecting.')
        if test_settings is not None and 'private_key' in test_settings.model_fields_set:
            if test_settings.private_key is None:
                raise HTTPException(409, 'Enter an SSH private key for this agent before connecting.')
            secret = test_settings.private_key.get_secret_value()
        else:
            credential = conn.execute('''SELECT c.id,c.project_id,c.ciphertext FROM ka.servers s
                LEFT JOIN ka.agent_server_connections a ON a.server_id=s.id AND a.agent_id=%s
                JOIN ka.credentials c ON c.id=CASE WHEN a.server_id IS NULL THEN s.credential_id ELSE a.credential_id END
                WHERE s.id=%s''', (self.agent_id,server_id)).fetchone()
            if credential is None:
                raise HTTPException(409, 'Save an SSH private key for this agent before connecting.')
            try:
                encrypted = bytes(credential['ciphertext'])
                secret = self.db.cipher.decrypt(encrypted[:12], encrypted[12:], f"{credential['project_id']}:{credential['id']}:ssh_private_key:v1".encode()).decode()
            except Exception:
                raise HTTPException(503, 'Could not decrypt this agent SSH credential.') from None
        return SSHConnection(row['hostname'],row['port'],row['username'],row['known_hosts'],secret,row['allow_sudo'])

    def audit(self, conn, actor, action, resource_id, details):
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'operations',%s,%s)''', (self.project(conn), actor, action, resource_id, Jsonb(details)))

    def save_ssh(self, server_id, value: SSHInput, identity):
        with self.db.connect() as conn:
            project = self.project(conn)
            row = conn.execute('SELECT * FROM ka.servers WHERE id=%s AND project_id=%s FOR UPDATE', (server_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Server not found')
            if row['revision'] != value.revision:
                raise HTTPException(409, 'Server settings changed. Close and reopen before saving.')
            actor = self.db.actor(conn, *identity)
            credential = row['credential_id']
            if value.private_key is not None:
                if credential is not None and conn.execute('SELECT 1 FROM ka.agent_server_connections WHERE credential_id=%s', (credential,)).fetchone() is not None:
                    credential = None
                credential = credential or uuid4()
                nonce = os.urandom(12)
                ciphertext = nonce + self.db.cipher.encrypt(nonce, value.private_key.get_secret_value().encode(), f'{project}:{credential}:ssh_private_key:v1'.encode())
                conn.execute('''INSERT INTO ka.credentials(id,project_id,name,kind,ciphertext,key_version,created_by)
                    VALUES (%s,%s,%s,'ssh_private_key',%s,'v1',%s) ON CONFLICT(id) DO UPDATE SET ciphertext=EXCLUDED.ciphertext''',
                    (credential, project, f'server-{server_id}-{credential}', ciphertext, actor))
            elif 'private_key' in value.model_fields_set:
                credential = None
            ready = credential is not None and value.username is not None
            conn.execute('''UPDATE ka.servers SET username=%s,port=%s,allow_sudo=%s,known_hosts=%s,
                credential_id=%s,auth_kind='ssh_private_key',enabled=%s,revision=revision+1,last_tested_at=NULL,last_test_error=NULL
                WHERE id=%s''', (value.username, value.port, value.allow_sudo, value.known_hosts, credential, ready, server_id))
            if credential is None and row['credential_id'] is not None:
                conn.execute('DELETE FROM ka.credentials WHERE id=%s AND NOT EXISTS (SELECT 1 FROM ka.agent_server_connections WHERE credential_id=%s)', (row['credential_id'],row['credential_id']))
            self.audit(conn, actor, 'server.ssh.updated', server_id, {'has_private_key': credential is not None, 'allow_sudo': value.allow_sudo, 'host_verification': bool(value.known_hosts.strip())})
            return self.server(conn, server_id)

    def connection(self, conn, server_id, *, test_settings: SSHInput | None = None) -> SSHConnection:
        row = conn.execute('''SELECT s.*,c.ciphertext FROM ka.servers s LEFT JOIN ka.credentials c ON c.id=s.credential_id
            WHERE s.id=%s AND s.project_id=%s''', (server_id, self.project(conn))).fetchone()
        if row is None or not row['ssh_allowed']:
            raise HTTPException(409, 'SSH is disabled or not configured for this server.')
        if test_settings is not None:
            if row['revision'] != test_settings.revision:
                raise HTTPException(409, 'Server settings changed. Reload before testing.')
            for name in ('username', 'port', 'allow_sudo', 'known_hosts'):
                if name in test_settings.model_fields_set:
                    row[name] = getattr(test_settings, name)
        elif not row['enabled']:
            raise HTTPException(409, 'SSH is disabled or not configured for this server.')
        if row['username'] is None:
            raise HTTPException(409, 'Set an SSH login user before testing.')
        if test_settings is not None and 'private_key' in test_settings.model_fields_set:
            if test_settings.private_key is None:
                raise HTTPException(409, 'Enter an SSH private key before testing.')
            secret = test_settings.private_key.get_secret_value()
        else:
            if row['ciphertext'] is None:
                raise HTTPException(409, 'Enter an SSH private key before testing.')
            try:
                encrypted = bytes(row['ciphertext'])
                secret = self.db.cipher.decrypt(encrypted[:12], encrypted[12:], f"{row['project_id']}:{row['credential_id']}:ssh_private_key:v1".encode()).decode()
            except Exception:
                raise HTTPException(503, 'Could not decrypt this server credential.') from None
        return SSHConnection(row['hostname'], row['port'], row['username'], row['known_hosts'] or '', secret, row['allow_sudo'])

    def save_settings(self, value: SettingsInput, identity):
        with self.db.connect() as conn:
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (self.agent_id,))
            current = self.settings(conn, lock=True)
            project = current['project_id']
            if current['revision'] != value.revision:
                raise HTTPException(409, 'Agent settings changed. Reload before saving.')
            servers = conn.execute('SELECT id FROM ka.servers WHERE project_id=%s AND ssh_allowed AND id=ANY(%s) FOR SHARE', (project, value.server_ids)).fetchall()
            if set(value.server_ids) != set(current['server_ids']) and len(servers) != len(value.server_ids):
                raise HTTPException(400, 'Select SSH-enabled servers.')
            skills = conn.execute('SELECT id FROM ka.agent_skills WHERE project_id=%s AND enabled AND id=ANY(%s) FOR SHARE', (project, value.skill_ids)).fetchall()
            if set(value.skill_ids) != set(current['skill_ids']) and len(skills) != len(value.skill_ids):
                raise HTTPException(400, 'Select enabled skills.')
            actor = self.db.actor(conn, *identity)
            conn.execute('UPDATE ka.operations_settings SET revision=revision+1,updated_by=%s WHERE project_id=%s AND agent=%s', (actor, project,self.agent_id))
            conn.execute('DELETE FROM ka.operations_servers WHERE project_id=%s AND agent=%s', (project,self.agent_id))
            global_config = conn.execute("""UPDATE ka.agent_configs SET instructions=%s,revision=revision+1,updated_by=%s
                WHERE project_id=%s AND agent=%s AND server_id IS NULL RETURNING id""", (value.instructions, actor, project,self.agent_id)).fetchone()
            if 'mcp_server_ids' in value.model_fields_set and set(value.mcp_server_ids) != set(current['mcp_server_ids']):
                try:
                    save_selection(conn, project, global_config['id'], value.mcp_server_ids)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from None
            if set(value.skill_ids) != set(current['skill_ids']):
                conn.execute('UPDATE ka.agents SET revision=revision+1,updated_by=%s WHERE id=%s', (actor,self.agent_id))
            conn.execute('DELETE FROM ka.skills_agents WHERE agent_id=%s', (self.agent_id,))
            for server_id in value.server_ids:
                conn.execute('INSERT INTO ka.operations_servers(project_id,server_id,agent) VALUES (%s,%s,%s)', (project, server_id,self.agent_id))
            for skill_id in value.skill_ids:
                conn.execute('INSERT INTO ka.skills_agents(agent_id,skill_id,project_id) VALUES (%s,%s,%s)', (self.agent_id, skill_id, project))
            self.audit(conn, actor, 'operations.settings.updated', None, {'server_ids': [str(s) for s in value.server_ids]})
            return self.settings(conn)


    def save_server_config(self, server_id: UUID, value: ServerConfigInput, identity):
        with self.db.connect() as conn:
            settings = self.settings(conn, lock=True)
            if settings['revision'] != value.settings_revision:
                raise HTTPException(409, 'Agent settings changed. Reload before saving.')
            project = settings['project_id']
            server = conn.execute('SELECT ssh_allowed FROM ka.servers WHERE id=%s AND project_id=%s FOR SHARE', (server_id, project)).fetchone()
            if server is None:
                raise HTTPException(404, 'Server not found')
            if not server['ssh_allowed']:
                raise HTTPException(400, 'Enable SSH for this server before configuring the agent.')
            current = next((c for c in self.configs(conn) if c['server_id'] == server_id), None)
            if (current['revision'] if current is not None else None) != value.revision:
                raise HTTPException(409, 'Server configuration changed. Reload before saving.')
            skills = conn.execute('SELECT id FROM ka.agent_skills WHERE project_id=%s AND enabled AND id=ANY(%s) FOR SHARE', (project, value.skill_ids or [])).fetchall()
            if (current is None or value.skill_ids != current['skill_ids']) and len(skills) != len(value.skill_ids or []):
                raise HTTPException(400, 'Select enabled skills.')
            actor = self.db.actor(conn, *identity)
            row = conn.execute("""INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(project_id,agent,server_id) DO UPDATE SET instructions=EXCLUDED.instructions,
                inherit_skills=EXCLUDED.inherit_skills,revision=agent_configs.revision+1,updated_by=EXCLUDED.updated_by RETURNING id""",
                (project,self.agent_id,server_id,value.instructions,value.skill_ids is None,actor)).fetchone()
            if current is None or 'mcp_server_ids' in value.model_fields_set and value.mcp_server_ids != current['mcp_server_ids']:
                conn.execute('UPDATE ka.agent_configs SET inherit_mcp_servers=%s WHERE id=%s', (value.mcp_server_ids is None, row['id']))
                try:
                    save_selection(conn, project, row['id'], value.mcp_server_ids or [])
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from None
            conn.execute('DELETE FROM ka.agent_config_skills WHERE config_id=%s', (row['id'],))
            for skill_id in value.skill_ids or []:
                conn.execute('INSERT INTO ka.agent_config_skills VALUES (%s,%s,%s)', (row['id'], project, skill_id))
            conn.execute('UPDATE ka.operations_settings SET revision=revision+1,updated_by=%s WHERE project_id=%s AND agent=%s', (actor, project,self.agent_id))
            self.audit(conn, actor, 'operations.server_config.saved', server_id, {'config_id': str(row['id'])})
            return next(c for c in self.configs(conn) if c['server_id'] == server_id)

    def delete_server_config(self, server_id: UUID, value: DeleteServerConfigInput, identity):
        with self.db.connect() as conn:
            settings = self.settings(conn, lock=True)
            if settings['revision'] != value.settings_revision:
                raise HTTPException(409, 'Agent settings changed. Reload before removing the configuration.')
            current = next((c for c in self.configs(conn) if c['server_id'] == server_id), None)
            if current is None:
                raise HTTPException(404, 'Server configuration not found')
            if current['revision'] != value.revision:
                raise HTTPException(409, 'Server configuration changed. Reload before removing it.')
            actor = self.db.actor(conn, *identity)
            conn.execute('DELETE FROM ka.agent_configs WHERE id=%s', (current['id'],))
            conn.execute('UPDATE ka.operations_settings SET revision=revision+1,updated_by=%s WHERE project_id=%s AND agent=%s', (actor, settings['project_id'],self.agent_id))
            self.audit(conn, actor, 'operations.server_config.deleted', server_id, {'config_id': str(current['id'])})
            return {'deleted': True}
