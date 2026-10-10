"""Git repository settings, encrypted credentials, and durable synchronization jobs."""

import json
import os
import re
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from knowledge_admin import graph_sync
from knowledge_admin.code_paths import code_path, code_root, relative_code_path


def github_url(value: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"(?:https://github\.com/)?([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})/?", value)
    if match is None:
        raise ValueError("Use https://github.com/owner/repository or owner/repository, without credentials or query parameters")
    owner, name = match.groups()
    name = name.removesuffix(".git")
    if name in ("", ".", ".."):
        raise ValueError("Invalid GitHub repository name")
    return f"https://github.com/{owner.lower()}/{name.lower()}.git"


def git_url(value: str) -> str:
    if '://' not in value or value.startswith('https://github.com/'):
        return github_url(value)
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username is not None or url.password is not None
            or url.query or url.fragment or not url.path.strip('/') or any(c.isspace() for c in value)
            or '\\' in value or any(part in ('.', '..') for part in url.path.split('/'))):
        raise ValueError('Use an HTTPS Git repository URL without embedded credentials or query parameters.')
    return value.rstrip('/')


class RepositoryInput(BaseModel):
    remote_url: str | None = Field(max_length=2000)
    name: str = Field(default="", max_length=200)
    description: str = Field(default='', max_length=4000)
    requested_ref: str | None = Field(default=None, max_length=200)
    folder_paths: list[str] | None = Field(default=None, max_length=100)
    clone_path: str | None = Field(default=None, max_length=1000)
    clone_now: bool = False
    sync_interval_minutes: int | None = Field(default=None, ge=5, le=10080, strict=True)
    revision: int | None = Field(default=None, ge=1, strict=True)
    # Omitted fields preserve saved values; token=null removes both credentials.
    token: SecretStr | None = None
    username: SecretStr | None = None

    @field_validator("remote_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return git_url(value.strip()) if value is not None else None

    @field_validator('clone_path')
    @classmethod
    def validate_clone_path(cls, value):
        return relative_code_path(value) if value is not None else None

    @field_validator('folder_paths')
    @classmethod
    def validate_folders(cls, value):
        if value is not None:
            if len(value) != len(set(value)):
                raise ValueError('Select each folder only once.')
            for path in value:
                relative_code_path(path)
        return value

    @model_validator(mode='after')
    def source_settings(self):
        if self.remote_url is None:
            if not self.name.strip() or not self.folder_paths:
                raise ValueError('A local code project needs a name and at least one folder.')
            if self.clone_path is not None or self.clone_now or self.sync_interval_minutes is not None or self.token is not None or self.username is not None or self.requested_ref is not None:
                raise ValueError('Clone, token, branch and pull settings require a Git source.')
        elif self.folder_paths is not None and self.clone_path is None:
            raise ValueError('Choose the folder name for the Git clone.')
        if self.clone_path is not None and self.folder_paths is None:
            raise ValueError('Provide the code project folder mappings.')
        if self.username is not None and 'token' in self.model_fields_set and self.token is None:
            raise ValueError('Do not supply a username when removing Git authentication.')
        return self

    @field_validator('username')
    @classmethod
    def validate_username(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            username = value.get_secret_value()
            if not 1 <= len(username) <= 200 or ':' in username or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in username):
                raise ValueError('Enter a Git username without whitespace, colons or control characters.')
        return value

    @field_validator("requested_ref")
    @classmethod
    def validate_ref(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) is None
                or ".." in value or "//" in value
                or any(part.startswith(".") or part.endswith((".", ".lock")) for part in value.split("/"))
                or value.endswith("/")):
            raise ValueError("Use a branch, tag, or commit SHA")
        return value

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            secret = value.get_secret_value()
            if not 1 <= len(secret) <= 1024 or any(c.isspace() or ord(c) < 33 for c in secret):
                raise ValueError("Enter a token without whitespace")
        return value


# Explicit public projection: encrypted credentials never enter API responses.
REPOSITORY_FIELDS = """r.id, r.name, r.remote_url, r.requested_ref,
    r.current_commit, r.synced_at, r.checkout_path,r.description,r.clone_path,r.sync_interval_minutes,r.next_sync_at,r.revision,
    ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths,
    (r.credential_id IS NOT NULL) AS has_token,
    j.id AS job_id, j.status AS sync_status, j.error AS sync_error,
    j.created_at AS queued_at"""
LATEST_JOB = """LEFT JOIN LATERAL (
    SELECT id, status, error, created_at FROM ka.jobs
    WHERE repository_id = r.id AND kind = 'repository_sync'
    ORDER BY created_at DESC, id DESC LIMIT 1
) j ON true"""


class RepositoryStore:
    def __init__(self, conninfo: str, encryption_key: bytes, issuer: str):
        self.conninfo = conninfo
        self.cipher = AESGCM(encryption_key)
        self.issuer = issuer

    def connect(self):
        return psycopg.connect(self.conninfo, row_factory=dict_row, connect_timeout=5)

    def decrypt_git_credentials(self, credential: dict) -> tuple[str, str]:
        """Read the encrypted pair, retaining support for existing token-only records."""
        version = credential['key_version']
        if version not in ('v1', 'v2'):
            raise ValueError('Unsupported Git credential format')
        envelope = bytes(credential['ciphertext'])
        plaintext = self.cipher.decrypt(envelope[:12], envelope[12:],
            f"{credential['project_id']}:{credential['credential_id']}:git_https_token:{version}".encode()).decode()
        if version == 'v1':
            return 'x-access-token', plaintext
        values = json.loads(plaintext)
        if not isinstance(values, dict) or any(not isinstance(values.get(key), str) or not values[key] for key in ('username', 'token')):
            raise ValueError('Invalid Git credential pair')
        return values['username'], values['token']

    def project(self, conn, app: str) -> UUID:
        project_id = uuid5(NAMESPACE_URL, f"knowledge-agent-v3:application:{app}")
        slug = re.sub(r"[^a-z0-9]+", "-", app.lower()).strip("-") or "application"
        conn.execute("""INSERT INTO ka.projects(id, slug, name) VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING""", (project_id, f"{slug}-{project_id.hex[:8]}", app))
        return project_id

    def actor(self, conn, subject: str, email: str) -> UUID:
        return conn.execute("""INSERT INTO ka.principals(issuer, subject, email) VALUES (%s, %s, %s)
            ON CONFLICT (issuer, subject) DO UPDATE SET email=EXCLUDED.email, last_seen_at=now()
            RETURNING id""", (self.issuer, subject, email)).fetchone()["id"]

    def list(self, app: str) -> list[dict]:
        with self.connect() as conn:
            project_id = self.project(conn, app)
            return conn.execute(f"""SELECT {REPOSITORY_FIELDS} FROM ka.repositories r {LATEST_JOB}
                WHERE r.project_id=%s AND r.archived_at IS NULL ORDER BY r.created_at DESC""", (project_id,)).fetchall()

    def save(self, app: str, value: RepositoryInput, subject: str, email: str,
             repository_id: UUID | None = None) -> UUID:
        with self.connect() as conn:
            project_id = self.project(conn, app)
            actor_id = self.actor(conn, subject, email)
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'code-projects:{project_id}',))
            existing = None
            if repository_id is not None:
                existing = conn.execute("""SELECT * FROM ka.repositories
                    WHERE id=%s AND project_id=%s AND archived_at IS NULL FOR UPDATE""", (repository_id, project_id)).fetchone()
                if existing is None:
                    raise HTTPException(404, "Repository not found")
                self.require_idle(conn, repository_id)
                if value.revision is not None and value.revision != existing['revision']:
                    raise HTTPException(409, 'Code project changed. Reload before saving.')
            else:
                repository_id = uuid4()
            if value.folder_paths is not None:
                root = code_root()
                folders = list(dict.fromkeys([*value.folder_paths, *([value.clone_path] if value.clone_path else [])]))
                for path in folders:
                    target = code_path(root, path)
                    if path != value.clone_path and not target.is_dir():
                        raise HTTPException(400, f'Folder does not exist inside the code root: {path}')
                if value.clone_path is not None:
                    target = code_path(root, value.clone_path)
                    previous_path = existing['clone_path'] or existing['checkout_path'] if existing is not None else None
                    if target.exists() and value.clone_path != previous_path:
                        raise HTTPException(409, 'The clone folder already exists. Choose an unused folder name.')
                    if existing is not None and existing['current_commit'] is not None and value.clone_path != previous_path:
                        raise HTTPException(400, 'The existing clone folder cannot be moved by editing this project.')
                    occupied = conn.execute('''SELECT 1 FROM ka.code_project_folders
                        WHERE code_project_id<>%s AND project_id=%s AND
                            (path=%s OR starts_with(path,%s || '/') OR starts_with(%s,path || '/'))''',
                        (repository_id,project_id,value.clone_path,value.clone_path,value.clone_path)).fetchone()
                    if occupied is not None:
                        raise HTTPException(409, 'The clone folder overlaps another code project. Choose a different folder.')
            credential_id = existing["credential_id"] if existing is not None else None
            previous_credential = credential_id
            if 'token' in value.model_fields_set and value.token is None:
                credential_id = None
            elif value.token is not None or value.username is not None:
                username, token = 'x-access-token', None
                if credential_id is not None and (value.token is None or value.username is None):
                    saved = conn.execute('SELECT id AS credential_id,project_id,ciphertext,key_version FROM ka.credentials WHERE id=%s', (credential_id,)).fetchone()
                    try:
                        username, token = self.decrypt_git_credentials(saved)
                    except (InvalidTag, ValueError):
                        raise HTTPException(400, 'Saved Git authentication cannot be decrypted. Enter both username and token to replace it.') from None
                if value.username is not None:
                    username = value.username.get_secret_value()
                if value.token is not None:
                    token = value.token.get_secret_value()
                if token is None:
                    raise HTTPException(400, 'Provide an access token with the Git username.')
                credential_id = credential_id or uuid4()
                nonce = os.urandom(12)
                plaintext = json.dumps({'username': username, 'token': token}).encode()
                ciphertext = nonce + self.cipher.encrypt(nonce, plaintext,
                    f'{project_id}:{credential_id}:git_https_token:v2'.encode())
                conn.execute("""INSERT INTO ka.credentials(id, project_id, name, kind, ciphertext, key_version, created_by)
                    VALUES (%s, %s, %s, 'git_https_token', %s, 'v2', %s)
                    ON CONFLICT (id) DO UPDATE SET ciphertext=EXCLUDED.ciphertext, key_version='v2'""",
                    (credential_id, project_id, f'github-{repository_id}', ciphertext, actor_id))
            name = value.name.strip() or value.remote_url.removeprefix("https://github.com/").removesuffix(".git")
            if existing is None:
                conn.execute("""INSERT INTO ka.repositories(id, project_id, slug, name, remote_url, requested_ref, credential_id, credential_kind)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", (repository_id, project_id,
                    f"repository-{repository_id.hex}", name, value.remote_url, value.requested_ref,
                    credential_id, "git_https_token" if credential_id else None))
            else:
                # A URL change must never reuse the former repository's Git object cache or token.
                if existing["remote_url"] != value.remote_url:
                    raise HTTPException(400, "Add a new repository to use a different GitHub URL")
                conn.execute("""UPDATE ka.repositories SET name=%s, requested_ref=%s, credential_id=%s, credential_kind=%s
                    WHERE id=%s""", (name, value.requested_ref, credential_id, "git_https_token" if credential_id else None, repository_id))
            if previous_credential is not None and credential_id is None:
                conn.execute("DELETE FROM ka.credentials WHERE id=%s", (previous_credential,))
            if value.folder_paths is not None:
                conn.execute('DELETE FROM ka.code_project_folders WHERE code_project_id=%s', (repository_id,))
                for path in folders:
                    conn.execute('INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,%s)',
                        (repository_id,project_id,path))
                conn.execute('''UPDATE ka.repositories SET description=%s,clone_path=%s,sync_interval_minutes=%s,
                    next_sync_at=CASE WHEN %s::integer IS NULL THEN NULL ELSE now()+make_interval(mins=>%s) END
                    WHERE id=%s''', (value.description,value.clone_path,value.sync_interval_minutes,
                        value.sync_interval_minutes,value.sync_interval_minutes,repository_id))
            if existing is not None:
                conn.execute('UPDATE ka.repositories SET revision=revision+1 WHERE id=%s', (repository_id,))
            graph_sync.sync_inventory(conn, project_id)
            if value.clone_now:
                job_id = uuid4()
                conn.execute('''INSERT INTO ka.jobs(id,project_id,kind,repository_id,requested_by,idempotency_key)
                    VALUES (%s,%s,'repository_sync',%s,%s,%s)''', (job_id,project_id,repository_id,actor_id,str(job_id)))
            conn.execute("""INSERT INTO ka.audit_events(project_id, actor_id, action, resource_type, resource_id, details)
                VALUES (%s,%s,%s,'repository',%s,%s)""", (project_id, actor_id,
                "repository.created" if existing is None else "repository.updated", repository_id,
                Jsonb({"remote_url": value.remote_url, "requested_ref": value.requested_ref, "has_token": credential_id is not None})))
            return repository_id

    def require_idle(self, conn, repository_id: UUID) -> None:
        if conn.execute("""SELECT 1 FROM ka.jobs WHERE repository_id=%s
            AND status IN ('queued','running','waiting_input')""", (repository_id,)).fetchone() is not None:
            raise HTTPException(409, "Wait for the current pull to finish before editing this repository")

    def enqueue(self, app: str, repository_id: UUID, subject: str, email: str) -> UUID:
        with self.connect() as conn:
            project_id = self.project(conn, app)
            repo = conn.execute("""SELECT id,remote_url FROM ka.repositories WHERE id=%s AND project_id=%s
                AND archived_at IS NULL FOR UPDATE""", (repository_id, project_id)).fetchone()
            if repo is None:
                raise HTTPException(404, "Repository not found")
            if repo['remote_url'] is None:
                raise HTTPException(400, 'This code project has no Git source to pull.')
            live = conn.execute("""SELECT id FROM ka.jobs WHERE repository_id=%s
                AND status IN ('queued','running','waiting_input')""", (repository_id,)).fetchone()
            if live is not None:
                return live["id"]
            actor_id = self.actor(conn, subject, email)
            job_id = uuid4()
            conn.execute("""INSERT INTO ka.jobs(id, project_id, kind, repository_id, requested_by, idempotency_key)
                VALUES (%s,%s,'repository_sync',%s,%s,%s)""", (job_id, project_id, repository_id, actor_id, str(job_id)))
            conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id)
                VALUES (%s,%s,'repository.pull_requested','repository',%s)""", (project_id, actor_id, repository_id))
            conn.execute('''UPDATE ka.repositories SET next_sync_at=CASE WHEN sync_interval_minutes IS NULL THEN NULL
                ELSE now()+make_interval(mins=>sync_interval_minutes) END WHERE id=%s''', (repository_id,))
            return job_id
