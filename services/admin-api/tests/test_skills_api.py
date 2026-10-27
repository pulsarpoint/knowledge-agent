"""The skills API: skills are folders. Create from a template, import a zip or git, update from source or an upload,
save from the editor (a new folder from the current one plus changes), restore a kept folder, delete."""

import base64
import logging
from uuid import UUID

import pytest

from knowledge_admin.skills import imports
from test_api import _client
from test_skill_import import a_zip, git, repo, skill_md  # noqa: F401 (repo is a fixture)

HEADERS = {'X-Forwarded-Groups': 'docs-admins', 'X-Forwarded-User': 'admin', 'X-Forwarded-Email': 'admin@example.test'}
ZIP = {**HEADERS, 'Content-Type': 'application/zip'}
VALUE = {'name': 'inspect-logs', 'description': 'Use when a service fails.', 'instructions': '# Inspect\n\nRead.\n'}


@pytest.fixture
def root(skills_folder):
    return skills_folder


@pytest.fixture
def client(store, tmp_path, root):
    return _client(tmp_path, repository_store=store)


def create(client, value=VALUE) -> dict:
    response = client.post('/api/skills', headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_from_the_template_writes_a_folder_with_skill_md(client, root, store):
    skill = create(client)
    assert skill['name'] == 'inspect-logs' and skill['source'] == {'kind': 'editor'} and skill['revision'] == 1
    assert skill['folder'] == f'skills/{skill["id"]}/{skill["version"]}'
    text = (root / skill['folder'] / 'SKILL.md').read_text()
    assert text.startswith('---\nname: inspect-logs\ndescription: Use when a service fails.\n---\n\n# Inspect')
    assert skill['files'] == [{'path': 'SKILL.md', 'size': len(text.encode()), 'executable': False}]
    assert [v['version'] for v in skill['versions']] == [skill['version']] and skill['versions'][0]['current']
    assert client.get('/api/skills', headers=HEADERS).json()['skills'][0]['version'] == skill['version']
    templated = create(client, {'name': 'blank', 'description': 'A new skill.'})
    assert '# blank' in client.get(f'/api/skills/{templated["id"]}/files/SKILL.md', headers=HEADERS).json()['text']
    with store.connect() as conn:
        actions = [r['action'] for r in conn.execute("SELECT action FROM ka.audit_events WHERE resource_type='skill'")]
    assert actions == ['skill.created', 'skill.created']


def test_a_name_clash_is_refused_and_nothing_is_written(client, root):
    skill = create(client)
    refused = client.post('/api/skills', headers=HEADERS, json=VALUE)
    assert refused.status_code == 409
    assert refused.json()['error']['message'] == 'A skill named inspect-logs exists; update it instead.'
    imported = client.post(
        '/api/skills/import/zip?file=a.zip', headers=ZIP, content=a_zip({'SKILL.md': skill_md('inspect-logs')})
    )
    assert imported.status_code == 409
    assert imported.json()['error']['message'] == 'A skill named inspect-logs exists; update it instead.'
    assert [p.name for p in (root / 'skills').iterdir()] == [skill['id']]


def test_import_a_zip_with_several_skills_previews_then_imports(client, root):
    data = a_zip({'pdf/SKILL.md': skill_md('pdf'), 'docx/SKILL.md': skill_md('docx'), 'docx/r.md': b'r'})
    preview = client.post('/api/skills/import/zip?file=all.zip&preview=true', headers=ZIP, content=data)
    assert preview.status_code == 200, preview.text
    assert [(s['name'], [f['path'] for f in s['files']]) for s in preview.json()['skills']] == [
        ('docx', ['SKILL.md', 'r.md']),
        ('pdf', ['SKILL.md']),
    ]
    assert not any((root / 'skills').iterdir())
    done = client.post('/api/skills/import/zip?file=all.zip', headers=ZIP, content=data)
    assert done.status_code == 201, done.text
    assert [(s['name'], s['source']) for s in done.json()['skills']] == [
        ('docx', {'kind': 'upload', 'file': 'all.zip'}),
        ('pdf', {'kind': 'upload', 'file': 'all.zip'}),
    ]


def test_a_zip_with_an_escape_or_a_symlink_writes_nothing(client, root):
    """Review Focus 2."""
    for data in (
        a_zip({'SKILL.md': skill_md('pdf'), '../evil': b'x'}),
        a_zip({'SKILL.md': skill_md('pdf'), 'link': b'/etc/passwd'}, symlinks={'link'}),
    ):
        response = client.post('/api/skills/import/zip?file=x.zip', headers=ZIP, content=data)
        assert response.status_code == 400, response.text
    assert not any((root / 'skills').iterdir()) and not (root / 'evil').exists()
    assert client.get('/api/skills', headers=HEADERS).json()['skills'] == []


def test_git_import_then_update_from_source_writes_a_new_folder_and_lists_changes(client, root, repo, monkeypatch):
    """Review Focus 3."""
    monkeypatch.setattr(imports, 'ALLOW_LOCAL', True)
    body = {'url': str(repo), 'ref': 'main', 'path': 'skills/pdf-forms'}
    [skill] = client.post('/api/skills/import/git', headers=HEADERS, json=body).json()['skills']
    first = git(repo, 'rev-parse', 'main')
    assert skill['source'] == {
        'kind': 'git',
        'url': str(repo),
        'ref': 'main',
        'path': 'skills/pdf-forms',
        'commit': first,
    }
    assert skill['version'] == first[:12]
    assert (root / skill['folder'] / 'scripts/fill.sh').stat().st_mode & 0o100
    (repo / 'skills/pdf-forms/ref.md').write_text('more')
    (repo / 'skills/pdf-forms/scripts/fill.sh').write_text('echo changed\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'change')
    updated = client.post(f'/api/skills/{skill["id"]}/update/source', headers=HEADERS, json={'revision': 1})
    assert updated.status_code == 200, updated.text
    second = git(repo, 'rev-parse', 'main')
    assert updated.json()['changed'] == [
        {'path': 'ref.md', 'change': 'added'},
        {'path': 'scripts/fill.sh', 'change': 'modified'},
    ]
    new = updated.json()['skill']
    assert new['version'] == second[:12] and new['source']['commit'] == second and new['revision'] == 2
    assert [v['version'] for v in new['versions']] == [second[:12], first[:12]]
    assert (root / skill['folder'] / 'SKILL.md').exists()  # kept for Restore
    stale = client.post(f'/api/skills/{skill["id"]}/update/source', headers=HEADERS, json={'revision': 1})
    assert stale.status_code == 409  # checked before cloning, and again under the lock
    assert (
        client.post(f'/api/skills/{skill["id"]}/restore', headers=HEADERS, json={'version': first[:12]}).status_code
        == 400
    )
    restored = client.post(
        f'/api/skills/{skill["id"]}/restore', headers=HEADERS, json={'version': first[:12], 'revision': 2}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()['folder'] == skill['folder'] and restored.json()['source']['commit'] == first
    editor = create(client, {'name': 'other', 'description': 'Other.'})
    refused = client.post(f'/api/skills/{editor["id"]}/update/source', headers=HEADERS, json={'revision': 1})
    assert refused.status_code == 400


def test_update_from_upload_replaces_the_files(client, root):
    skill = create(client)
    data = a_zip({'SKILL.md': skill_md('inspect-logs', 'New scope.'), 'notes.md': b'n'})
    response = client.post(f'/api/skills/{skill["id"]}/update/upload?file=v2.zip&revision=1', headers=ZIP, content=data)
    assert response.status_code == 200, response.text
    assert response.json()['skill']['description'] == 'New scope.'
    assert response.json()['skill']['source'] == {'kind': 'upload', 'file': 'v2.zip'}
    assert {c['path'] for c in response.json()['changed']} == {'SKILL.md', 'notes.md'}
    other = create(client, {'name': 'other', 'description': 'Other.'})
    clash = client.post(
        f'/api/skills/{other["id"]}/update/upload?file=v2.zip&revision=1', headers=ZIP, content=data
    )  # SKILL.md names another skill
    assert clash.status_code == 409


def test_editor_save_is_a_new_folder_from_the_current_one_plus_changes(client, root):
    skill = create(client)
    endpoint = f'/api/skills/{skill["id"]}/files'
    changes = [
        {'path': 'scripts/run.sh', 'text': 'echo hi\n', 'executable': True},
        {'path': 'assets/logo.png', 'content_base64': base64.b64encode(b'\x89PNG').decode()},
        {'path': 'notes.md', 'text': 'n'},
    ]
    saved = client.put(endpoint, headers=HEADERS, json={'revision': 1, 'changes': changes})
    assert saved.status_code == 200, saved.text
    new = saved.json()['skill']
    assert new['folder'] != skill['folder'] and new['source'] == {'kind': 'editor'} and new['revision'] == 2
    assert (root / new['folder'] / 'scripts/run.sh').stat().st_mode & 0o100
    assert client.get(f'/api/skills/{skill["id"]}/files/assets/logo.png', headers=HEADERS).json() == {
        'path': 'assets/logo.png',
        'size': 4,
        'executable': False,
        'text': None,
    }
    renamed = client.put(
        endpoint,
        headers=HEADERS,
        json={
            'revision': 2,
            'changes': [{'path': 'scripts', 'rename_to': 'bin'}, {'path': 'notes.md', 'delete': True}],
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert [f['path'] for f in renamed.json()['skill']['files']] == ['SKILL.md', 'assets/logo.png', 'bin/run.sh']
    stale = client.put(endpoint, headers=HEADERS, json={'revision': 2, 'changes': []})
    assert stale.status_code == 409
    for bad in (
        {'path': 'SKILL.md', 'text': 'no frontmatter'},
        {'path': '../evil', 'text': 'x'},
        {'path': 'big.bin', 'content_base64': base64.b64encode(b'x' * (5 * 1024 * 1024 + 1)).decode()},
        {'path': 'missing.md', 'delete': True},
    ):
        response = client.put(endpoint, headers=HEADERS, json={'revision': 3, 'changes': [bad]})
        assert response.status_code == 400, (bad['path'], response.text)
    assert client.get(f'/api/skills/{skill["id"]}', headers=HEADERS).json()['revision'] == 3


def test_saves_keep_the_last_three_folders(client, root):
    skill = create(client)
    for revision in range(1, 5):
        client.put(
            f'/api/skills/{skill["id"]}/files',
            headers=HEADERS,
            json={'revision': revision, 'changes': [{'path': 'n.md', 'text': str(revision)}]},
        )
    detail = client.get(f'/api/skills/{skill["id"]}', headers=HEADERS).json()
    assert len(detail['versions']) == 3 and detail['versions'][0]['current']
    assert len([p for p in (root / 'skills' / skill['id']).iterdir() if p.is_dir()]) == 3


def test_delete_removes_the_row_and_its_folders(client, root, store):
    skill = create(client)
    assert client.delete(f'/api/skills/{skill["id"]}', headers=HEADERS).status_code == 204
    assert client.get(f'/api/skills/{skill["id"]}', headers=HEADERS).status_code == 404
    assert not (root / 'skills' / skill['id']).exists()
    with store.connect() as conn:
        assert (
            conn.execute('SELECT count(*) AS n FROM ka.agent_skills WHERE id=%s', (UUID(skill['id']),)).fetchone()['n']
            == 0
        )


def test_status_and_file_reads(client):
    skill = create(client)
    disabled = client.patch(
        f'/api/skills/{skill["id"]}/status', headers=HEADERS, json={'enabled': False, 'revision': 1}
    )
    assert disabled.status_code == 200 and disabled.json()['enabled'] is False and disabled.json()['revision'] == 2
    assert client.get('/api/skills?enabled=true', headers=HEADERS).json()['skills'] == []
    assert client.get(f'/api/skills/{skill["id"]}/files/nope.md', headers=HEADERS).status_code == 404
    assert client.get(f'/api/skills/{skill["id"]}/files/../../etc/passwd', headers=HEADERS).status_code in (400, 404)


def test_without_a_skills_folder_the_api_says_so(store, tmp_path, monkeypatch):
    monkeypatch.delenv('SKILLS_ROOT', raising=False)
    response = _client(tmp_path, repository_store=store).post('/api/skills', headers=HEADERS, json=VALUE)
    assert response.status_code == 503


def test_a_failed_switch_leaves_no_new_folder_and_the_kept_folders_unchanged(client, root, monkeypatch, caplog):
    from knowledge_admin.skills import store

    skill = create(client)
    for revision in (1, 2):
        client.put(
            f'/api/skills/{skill["id"]}/files',
            headers=HEADERS,
            json={'revision': revision, 'changes': [{'path': 'n.md', 'text': str(revision)}]},
        )
    before = client.get(f'/api/skills/{skill["id"]}', headers=HEADERS).json()
    on_disk = sorted(p.name for p in (root / 'skills' / skill['id']).iterdir())

    def broken(*_, **__):
        raise RuntimeError('the database went away')

    caplog.set_level(logging.ERROR, logger='knowledge_admin.http')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'switch', broken)
        failed = client.put(
            f'/api/skills/{skill["id"]}/files',
            headers=HEADERS,
            json={'revision': 3, 'changes': [{'path': 'n.md', 'text': 'lost'}]},
        )
    assert failed.status_code == 500 and failed.json() == {'error': {'message': 'Internal error'}}
    [logged] = [r for r in caplog.records if r.name == 'knowledge_admin.http' and r.levelno == logging.ERROR]
    assert logged.exc_info[0] is RuntimeError and 'the database went away' in str(logged.exc_info[1])  # the switch
    assert client.get(f'/api/skills/{skill["id"]}', headers=HEADERS).json() == before
    assert sorted(p.name for p in (root / 'skills' / skill['id']).iterdir()) == on_disk


def test_changes_need_the_revision_and_an_upload_fits_25_mb(client):
    skill = create(client)
    data = a_zip({'SKILL.md': skill_md('inspect-logs')})
    assert client.post(f'/api/skills/{skill["id"]}/update/upload', headers=ZIP, content=data).status_code == 400
    big = {**ZIP, 'Content-Length': str(26 * 1024 * 1024)}
    assert client.post('/api/skills/import/zip', headers=big, content=b'x').status_code == 413


def test_a_chunked_upload_past_the_limit_is_refused_while_it_streams(client, monkeypatch):
    import asyncio

    import httpx

    from knowledge_admin.skills import api

    monkeypatch.setattr(api, 'MAX_UPLOAD', 1000)
    sent = []

    async def chunks():  # no Content-Length: the body comes chunked
        for _ in range(50):
            sent.append(1)
            yield b'x' * 100

    async def post():  # the ASGI transport streams the body as the app reads it (TestClient reads it all first)
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as http:
            return await http.post('/api/skills/import/zip?file=a.zip', headers=ZIP, content=chunks())

    refused = asyncio.run(post())
    assert refused.status_code == 413
    assert refused.json()['error']['message'] == 'An upload can be at most 25 MB.'
    assert len(sent) < 50  # it stopped reading past the limit


def test_a_github_tree_url_is_split_into_url_ref_and_path(client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        imports,
        'from_git',
        lambda url, ref, path, **_: (
            seen.append((url, ref, path)) or ({'name': 'x', 'description': 'd', 'files': {}}, 'c')
        ),
    )
    for body in (
        {'url': 'https://github.com/acme/skills/tree/main/skills/pdf-forms'},
        {'url': 'https://github.com/acme/skills/tree/main/skills/pdf-forms/', 'path': 'other'},
        {'url': 'https://github.com/acme/skills', 'ref': 'v1', 'path': 'p'},
    ):
        client.post('/api/skills/import/git', headers=HEADERS, json={**body, 'preview': True})
    assert seen == [
        ('https://github.com/acme/skills', 'main', 'skills/pdf-forms'),
        ('https://github.com/acme/skills', 'main', 'other'),  # an explicit path wins
        ('https://github.com/acme/skills', 'v1', 'p'),
    ]
