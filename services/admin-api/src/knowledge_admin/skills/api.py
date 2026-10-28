"""The skills API: a skill is a folder (SKILL.md and its files) on disk, its row the metadata. Every change writes a
complete new folder, then switches the row to it; the last three folders are kept for Restore."""

import base64
import binascii
import re
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_admin.db import audit, storage, workspace
from knowledge_admin.identity import request_actor
from knowledge_admin.repositories import RepositoryStore, git_url
from knowledge_admin.skills import imports, rules, store
from knowledge_admin.skills.rules import check_files, skill_md
from knowledge_admin.skills.store import FIELDS, Files

MAX_UPLOAD = 25 * 1024 * 1024
STALE = 'This skill changed since you opened it. Reopen it before saving.'
NOT_FOUND = 'Skill not found'


def clash(name: str) -> str:
    return f'A skill named {name} exists; update it instead.'


class NewSkill(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    instructions: str | None = Field(default=None, max_length=100000)


class GitSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=2000)
    ref: str = Field(default='', max_length=200)
    path: str = Field(default='', max_length=500)
    preview: bool = False

    @model_validator(mode='after')
    def split_github_tree(self):
        """A pasted `https://github.com/<owner>/<repo>/tree/<ref>/<path>` is URL, ref and path (a ref with slashes
        goes in Ref); a ref or path given in the form wins."""
        match = re.fullmatch(r'(https://github\.com/[^/\s]+/[^/\s]+)/tree/([^/\s]+)(?:/(.*))?', self.url.strip())
        if match:
            self.url = match[1]
            self.ref = self.ref or match[2]
            self.path = self.path or (match[3] or '').strip('/')
        return self


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


class RestoreInput(Revision):
    version: str = Field(min_length=1, max_length=100)


class StatusInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)


class FileChange(BaseModel):
    """One editor change: write a file (text or base64 content), delete a file or folder, or rename one."""

    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=1000)
    text: str | None = None
    content_base64: str | None = None
    executable: bool | None = None
    delete: bool = False
    rename_to: str | None = Field(default=None, min_length=1, max_length=1000)


class SaveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    changes: list[FileChange] = Field(max_length=1000)


def apply_changes(files: Files, changes: list[FileChange]) -> Files:
    """The current files with the editor's changes. Raises ValueError for a change that names no file."""
    files = dict(files)
    for change in changes:
        under = [path for path in files if path == change.path or path.startswith(change.path + '/')]
        if change.delete or change.rename_to is not None:
            if not under:
                raise ValueError(f'{change.path} is not in this skill.')
            for path in under:
                value = files.pop(path)
                if change.rename_to is not None:
                    files[change.rename_to + path[len(change.path) :]] = value
            continue
        if (change.text is None) == (change.content_base64 is None):
            raise ValueError(f'Give {change.path} either text or content.')
        try:
            content = (
                change.text.encode('utf-8')
                if change.text is not None
                else base64.b64decode(change.content_base64, validate=True)
            )
        except (binascii.Error, ValueError):
            raise ValueError(f'The content of {change.path} is not base64.') from None
        executable = change.executable if change.executable is not None else files.get(change.path, (b'', False))[1]
        files[change.path] = (content, executable)
    return files


def build_api(app_name: str, repository_store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/skills')
    database = partial(storage, repository_store, 'Skill storage', message='Skill storage is not configured')

    def root() -> Path:
        try:
            folder = store.skills_root()
        except ValueError as error:
            raise HTTPException(503, str(error)) from None
        if not folder.is_dir():
            raise HTTPException(503, 'The skills folder is unavailable.')
        return folder

    def summary(row: dict) -> dict:
        return {**row, 'version': PurePosixPath(row['folder']).name}

    def detail(skills_root: Path, row: dict) -> dict:
        current = PurePosixPath(row['folder']).name
        versions = [
            {
                **item,
                'folder': store.relative(skills_root, str(row['id']), item['version']),
                'current': item['version'] == current,
            }
            for item in store.kept_folders(skills_root, str(row['id']))
        ]
        return {**summary(row), 'files': store.list_files(store.absolute(row['folder'])), 'versions': versions}

    def locked(conn, project, skill_id, revision: int | None) -> dict:
        row = conn.execute(
            f'SELECT {FIELDS} FROM ka.agent_skills WHERE id=%s AND project_id=%s FOR UPDATE', (skill_id, project)
        ).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        if revision is not None and revision != row['revision']:
            raise HTTPException(409, STALE)
        return row

    def name_taken(conn, project, name: str, other_than=None) -> bool:
        return (
            conn.execute(
                'SELECT 1 FROM ka.agent_skills WHERE project_id=%s AND name=%s AND id IS DISTINCT FROM %s',
                (project, name, other_than),
            ).fetchone()
            is not None
        )

    def cleanup(conn, skills_root: Path, skill_id: str, current: str | None) -> None:
        store.cleanup(skills_root, {skill_id: current}, store.pinned_folders(conn))

    def create_skills(request: Request, found: list[dict], source: dict, commit: str | None = None) -> list[dict]:
        """Insert each skill after writing its folder; a name clash refuses them all before anything is written."""
        skills_root = root()
        subject, email = request_actor(request)
        db = database()
        with workspace(db, app_name) as (conn, project):
            for skill in found:
                if name_taken(conn, project, skill['name']):
                    raise HTTPException(409, clash(skill['name']))
            actor = db.actor(conn, subject, email)
            created, written = [], []
            try:
                for skill in found:
                    skill_id = str(uuid4())
                    version = store.write_folder(skills_root, skill_id, skill['files'], source, commit=commit)
                    written.append(skill_id)
                    row = conn.execute(
                        f"""INSERT INTO ka.agent_skills(id,project_id,name,description,source,folder,created_by,updated_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {FIELDS}""",
                        (
                            skill_id,
                            project,
                            skill['name'],
                            skill['description'],
                            Jsonb(source),
                            store.relative(skills_root, skill_id, version),
                            actor,
                            actor,
                        ),
                    ).fetchone()
                    audit(
                        conn,
                        project,
                        actor,
                        'skill.created',
                        'skill',
                        row['id'],
                        {'name': row['name'], 'source': source},
                    )
                    created.append(row)
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                for skill_id in written:
                    store.cleanup(skills_root, {skill_id: None}, set())
                raise HTTPException(409, clash(found[len(created)]['name'])) from None
            except BaseException:
                for skill_id in written:
                    store.cleanup(skills_root, {skill_id: None}, set())
                raise
            return [detail(skills_root, row) for row in created]

    def update_skill(request: Request, skill_id: UUID, revision: int, make, action: str) -> dict:
        """Write the new folder `make(row, skills_root)` returns ((files, source, commit)), then switch to it."""
        skills_root = root()
        subject, email = request_actor(request)
        db = database()
        with workspace(db, app_name) as (conn, project):
            row = locked(conn, project, skill_id, revision)
            files, source, commit = make(row, skills_root)
            try:
                meta = check_files(files)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
            if name_taken(conn, project, meta['name'], skill_id):
                raise HTTPException(409, clash(meta['name']))
            old = store.read_folder(skills_root, str(skill_id), PurePosixPath(row['folder']).name)
            kept = {item['version'] for item in store.kept_folders(skills_root, str(skill_id))}
            version = None
            try:
                version = store.write_folder(skills_root, str(skill_id), files, source, commit=commit)
                actor = db.actor(conn, subject, email)
                row = store.switch(
                    conn,
                    project,
                    skill_id,
                    folder=store.relative(skills_root, str(skill_id), version),
                    name=meta['name'],
                    description=meta['description'],
                    source=source,
                    actor=actor,
                )
                audit(conn, project, actor, action, 'skill', skill_id, {'name': row['name'], 'version': version})
                conn.commit()
            except BaseException:  # the previous folder stays current; the new one never becomes a version
                if version is not None and version not in kept:
                    store.remove_folder(skills_root, str(skill_id), version)
                raise
            cleanup(conn, skills_root, str(skill_id), version)
            return {'skill': detail(skills_root, row), 'changed': rules.changed_files(old, files)}

    def git_credentials(conn, project, url: str) -> tuple[str | None, str | None]:
        """The code project credential for this URL, when a code project with that remote has one."""
        try:
            remote = git_url(url)
        except ValueError:
            return None, None
        row = conn.execute(
            """SELECT r.project_id,r.credential_id,c.ciphertext,c.key_version FROM ka.repositories r
            JOIN ka.credentials c ON c.id=r.credential_id
            WHERE r.project_id=%s AND r.remote_url=%s AND r.archived_at IS NULL LIMIT 1""",
            (project, remote),
        ).fetchone()
        return database().decrypt_git_credentials(row) if row is not None else (None, None)

    def from_git(url: str, ref: str, path: str):
        with workspace(database(), app_name) as (conn, project):
            username, token = git_credentials(conn, project, url)
        try:
            return imports.from_git(url, ref, path, username=username, token=token)
        except imports.ImportRefused as error:
            raise HTTPException(400, str(error)) from None

    async def zip_body(request: Request) -> bytes:
        declared = request.headers.get('content-length', '')
        if declared.isdigit() and int(declared) > MAX_UPLOAD:
            raise HTTPException(413, 'An upload can be at most 25 MB.')
        data = bytearray()
        async for chunk in request.stream():  # a chunked body has no Content-Length: count while it comes
            data.extend(chunk)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, 'An upload can be at most 25 MB.')
        return bytes(data)

    def zip_skills(data: bytes) -> list[dict]:
        try:
            return imports.skills_in_zip(data)
        except imports.ImportRefused as error:
            raise HTTPException(400, str(error)) from None

    def preview(found: list[dict]) -> dict:
        return {
            'skills': [
                {
                    'name': skill['name'],
                    'description': skill['description'],
                    'files': [
                        {'path': path, 'size': len(content)} for path, (content, _) in sorted(skill['files'].items())
                    ],
                }
                for skill in found
            ]
        }

    @router.get('')
    def list_skills(enabled: bool | None = None) -> dict:
        with workspace(database(), app_name) as (conn, project):
            rows = conn.execute(
                f"""SELECT {FIELDS} FROM ka.agent_skills
                WHERE project_id=%s AND (%s::boolean IS NULL OR enabled=%s) ORDER BY name,id""",
                (project, enabled, enabled),
            ).fetchall()
        return {'skills': [summary(row) for row in rows]}

    @router.get('/{skill_id}')
    def get_skill(skill_id: UUID) -> dict:
        with workspace(database(), app_name) as (conn, project):
            row = conn.execute(
                f'SELECT {FIELDS} FROM ka.agent_skills WHERE id=%s AND project_id=%s', (skill_id, project)
            ).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return detail(root(), row)

    @router.post('', status_code=201)
    def create_skill(value: NewSkill, request: Request) -> dict:
        body = value.instructions or f'# {value.name}\n\nDescribe the steps to follow, and the files to read or run.\n'
        files = {'SKILL.md': (skill_md(value.name, value.description.strip(), body).encode(), False)}
        try:
            meta = check_files(files)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        [created] = create_skills(request, [{**meta, 'files': files}], {'kind': 'editor'})
        return created

    @router.post('/import/zip')
    async def import_zip(
        request: Request,
        response: Response,
        file: str = Query(default='', max_length=255),
        preview_only: bool = Query(default=False, alias='preview'),
    ) -> dict:
        found = zip_skills(await zip_body(request))
        if preview_only:
            return preview(found)
        response.status_code = 201
        return {'skills': create_skills(request, found, {'kind': 'upload', 'file': file})}

    @router.post('/import/git')
    def import_git(value: GitSource, request: Request, response: Response) -> dict:
        found, commit = from_git(value.url, value.ref, value.path)
        if value.preview:
            return preview([found])
        source = {'kind': 'git', 'url': value.url, 'ref': value.ref, 'path': value.path, 'commit': commit}
        response.status_code = 201
        return {'skills': create_skills(request, [found], source, commit)}

    @router.post('/{skill_id}/update/source')
    def update_from_source(skill_id: UUID, value: Revision, request: Request) -> dict:
        with workspace(database(), app_name) as (conn, project):
            row = conn.execute(
                'SELECT source,revision FROM ka.agent_skills WHERE id=%s AND project_id=%s', (skill_id, project)
            ).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        source = row['source']
        if source.get('kind') != 'git':
            raise HTTPException(400, 'Only a skill imported from git updates from its source.')
        if row['revision'] != value.revision:
            raise HTTPException(409, STALE)
        # Clone before taking the row lock; the revision, checked again under the lock, says the source is the same.
        found, commit = from_git(source['url'], source.get('ref', ''), source.get('path', ''))

        def make(*_):
            return found['files'], {**source, 'commit': commit}, commit

        return update_skill(request, skill_id, value.revision, make, 'skill.updated')

    @router.post('/{skill_id}/update/upload')
    async def update_from_upload(
        skill_id: UUID,
        request: Request,
        file: str = Query(default='', max_length=255),
        revision: int = Query(ge=1),
    ) -> dict:
        found = zip_skills(await zip_body(request))
        if len(found) != 1:
            raise HTTPException(400, 'Upload one skill to update this skill.')
        return update_skill(
            request,
            skill_id,
            revision,
            lambda *_: (found[0]['files'], {'kind': 'upload', 'file': file}, None),
            'skill.updated',
        )

    @router.put('/{skill_id}/files')
    def save_files(skill_id: UUID, value: SaveInput, request: Request) -> dict:
        def make(row, skills_root):
            current = store.read_folder(skills_root, str(skill_id), PurePosixPath(row['folder']).name)
            try:
                return apply_changes(current, value.changes), {'kind': 'editor'}, None
            except ValueError as error:
                raise HTTPException(400, str(error)) from None

        return update_skill(request, skill_id, value.revision, make, 'skill.saved')

    @router.post('/{skill_id}/restore')
    def restore(skill_id: UUID, value: RestoreInput, request: Request) -> dict:
        skills_root = root()
        subject, email = request_actor(request)
        db = database()
        with workspace(db, app_name) as (conn, project):
            row = locked(conn, project, skill_id, value.revision)
            kept = next(
                (k for k in store.kept_folders(skills_root, str(skill_id)) if k['version'] == value.version), None
            )
            if kept is None:
                raise HTTPException(404, 'This version is no longer kept.')
            try:
                meta = check_files(store.read_folder(skills_root, str(skill_id), value.version))
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
            if name_taken(conn, project, meta['name'], skill_id):
                raise HTTPException(409, clash(meta['name']))
            actor = db.actor(conn, subject, email)
            row = store.switch(
                conn,
                project,
                skill_id,
                folder=store.relative(skills_root, str(skill_id), value.version),
                name=meta['name'],
                description=meta['description'],
                source=kept['source'] or row['source'],
                actor=actor,
            )
            audit(
                conn,
                project,
                actor,
                'skill.restored',
                'skill',
                skill_id,
                {'name': row['name'], 'version': value.version},
            )
        return detail(skills_root, row)

    @router.delete('/{skill_id}', status_code=204)
    def delete_skill(skill_id: UUID, request: Request) -> Response:
        skills_root = root()
        subject, email = request_actor(request)
        db = database()
        with workspace(db, app_name) as (conn, project):
            row = locked(conn, project, skill_id, None)
            conn.execute('DELETE FROM ka.agent_skills WHERE id=%s', (skill_id,))
            actor = db.actor(conn, subject, email)
            audit(conn, project, actor, 'skill.deleted', 'skill', skill_id, {'name': row['name']})
            conn.commit()
            cleanup(conn, skills_root, str(skill_id), None)  # a folder a running run pinned goes at a later cleanup
        return Response(status_code=204)

    @router.get('/{skill_id}/files/{path:path}')
    def read_file(skill_id: UUID, path: str) -> dict:
        if rules.check_paths([{'path': path, 'type': 'file'}]):
            raise HTTPException(400, 'Use a path inside the skill folder.')
        with workspace(database(), app_name) as (conn, project):
            row = conn.execute(
                'SELECT folder FROM ka.agent_skills WHERE id=%s AND project_id=%s', (skill_id, project)
            ).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        file = store.absolute(row['folder']) / path
        if file.is_symlink() or not file.is_file():
            raise HTTPException(404, 'File not found')
        content, info = file.read_bytes(), file.stat()
        try:
            text = content.decode('utf-8')
        except UnicodeDecodeError:
            text = None
        return {'path': path, 'size': info.st_size, 'executable': bool(info.st_mode & 0o100), 'text': text}

    @router.patch('/{skill_id}/status')
    def set_status(skill_id: UUID, value: StatusInput, request: Request) -> dict:
        subject, email = request_actor(request)
        db = database()
        with workspace(db, app_name) as (conn, project):
            row = locked(conn, project, skill_id, None)
            if row['revision'] != value.revision:
                raise HTTPException(409, 'This skill changed. Refresh the list before changing its status')
            if row['enabled'] == value.enabled:
                return summary(row)
            actor = db.actor(conn, subject, email)
            row = conn.execute(
                f"""UPDATE ka.agent_skills SET enabled=%s,updated_by=%s,revision=revision+1
                WHERE id=%s AND project_id=%s RETURNING {FIELDS}""",
                (value.enabled, actor, skill_id, project),
            ).fetchone()
            action: Literal['skill.enabled', 'skill.disabled'] = 'skill.enabled' if value.enabled else 'skill.disabled'
            audit(conn, project, actor, action, 'skill', skill_id, {'name': row['name'], 'revision': row['revision']})
        return summary(row)

    return router
