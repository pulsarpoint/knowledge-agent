"""Server connection secrets: a server's own SSH settings."""

import os
from uuid import uuid4

import asyncssh
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from knowledge_admin.repositories import RepositoryStore
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
                raise ValueError(
                    'Enter a valid private key without a passphrase. It will be encrypted in storage.'
                ) from None
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


class OperationsStore:
    def __init__(self, store: RepositoryStore, app_name: str):
        self.db = store
        self.app_name = app_name

    def project(self, conn):
        return self.db.project(conn, self.app_name)

    def servers(self, conn):
        return conn.execute(
            """SELECT id,name,hostname,port,username,description,tags,ssh_allowed,enabled,allow_sudo,revision,
            coalesce(known_hosts,'') AS known_hosts,(credential_id IS NOT NULL) AS has_private_key,last_tested_at,last_test_error
            FROM ka.servers WHERE project_id=%s ORDER BY lower(name),id""",
            (self.project(conn),),
        ).fetchall()

    def server(self, conn, server_id):
        row = next((s for s in self.servers(conn) if s['id'] == server_id), None)
        if row is None:
            raise HTTPException(404, 'Server not found')
        return row

    def audit(self, conn, actor, action, resource_id, details):
        conn.execute(
            """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'operations',%s,%s)""",
            (self.project(conn), actor, action, resource_id, Jsonb(details)),
        )

    def save_ssh(self, server_id, value: SSHInput, identity):
        with self.db.connect() as conn:
            project = self.project(conn)
            row = conn.execute(
                'SELECT * FROM ka.servers WHERE id=%s AND project_id=%s FOR UPDATE', (server_id, project)
            ).fetchone()
            if row is None:
                raise HTTPException(404, 'Server not found')
            if row['revision'] != value.revision:
                raise HTTPException(409, 'Server settings changed. Close and reopen before saving.')
            actor = self.db.actor(conn, *identity)
            credential = row['credential_id']
            if value.private_key is not None:
                credential = credential or uuid4()
                nonce = os.urandom(12)
                ciphertext = nonce + self.db.cipher.encrypt(
                    nonce,
                    value.private_key.get_secret_value().encode(),
                    f'{project}:{credential}:ssh_private_key:v1'.encode(),
                )
                conn.execute(
                    """INSERT INTO ka.credentials(id,project_id,name,kind,ciphertext,key_version,created_by)
                    VALUES (%s,%s,%s,'ssh_private_key',%s,'v1',%s) ON CONFLICT(id) DO UPDATE SET ciphertext=EXCLUDED.ciphertext""",
                    (credential, project, f'server-{server_id}-{credential}', ciphertext, actor),
                )
            elif 'private_key' in value.model_fields_set:
                credential = None
            ready = credential is not None and value.username is not None
            conn.execute(
                """UPDATE ka.servers SET username=%s,port=%s,allow_sudo=%s,known_hosts=%s,
                credential_id=%s,auth_kind='ssh_private_key',enabled=%s,revision=revision+1,last_tested_at=NULL,last_test_error=NULL
                WHERE id=%s""",
                (value.username, value.port, value.allow_sudo, value.known_hosts, credential, ready, server_id),
            )
            if credential is None and row['credential_id'] is not None:
                conn.execute('DELETE FROM ka.credentials WHERE id=%s', (row['credential_id'],))
            self.audit(
                conn,
                actor,
                'server.ssh.updated',
                server_id,
                {
                    'has_private_key': credential is not None,
                    'allow_sudo': value.allow_sudo,
                    'host_verification': bool(value.known_hosts.strip()),
                },
            )
            return self.server(conn, server_id)

    def connection(self, conn, server_id, *, test_settings: SSHInput | None = None) -> SSHConnection:
        row = conn.execute(
            """SELECT s.*,c.ciphertext FROM ka.servers s LEFT JOIN ka.credentials c ON c.id=s.credential_id
            WHERE s.id=%s AND s.project_id=%s""",
            (server_id, self.project(conn)),
        ).fetchone()
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
                secret = self.db.cipher.decrypt(
                    encrypted[:12],
                    encrypted[12:],
                    f'{row["project_id"]}:{row["credential_id"]}:ssh_private_key:v1'.encode(),
                ).decode()
            except Exception:
                raise HTTPException(503, 'Could not decrypt this server credential.') from None
        return SSHConnection(
            row['hostname'], row['port'], row['username'], row['known_hosts'] or '', secret, row['allow_sudo']
        )
