import os
import subprocess
from pathlib import Path

import pytest

from knowledge_admin.code_projects import git_askpass, worker as repository_worker
from knowledge_admin.repositories import RepositoryInput


@pytest.fixture
def source(tmp_path, monkeypatch):
    repository = tmp_path / 'source'
    real_run = subprocess.run

    def git(*args):
        return real_run(
            ['git', '-C', str(repository), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    repository.mkdir()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Repository test')
    git('config', 'user.email', 'test@example.test')
    (repository / 'README.md').write_text('First version\n')
    git('add', '.')
    git('commit', '-m', 'First version')
    calls = []

    def transport(command, **kwargs):
        # Substitute only the network fetch; all cache, object copying, and checkouts use real Git.
        calls.append((command, kwargs['env'].copy()))
        if 'fetch' in command:
            command = list(command)
            command[command.index('origin')] = f'file://{repository}'
            command = [command[0], '-c', 'protocol.file.allow=always', *command[1:]]
        return real_run(command, **kwargs)

    monkeypatch.setattr(repository_worker.subprocess, 'run', transport)
    return repository, git, calls


def test_real_git_publishes_new_versions_and_failed_pull_keeps_previous(store, source, tmp_path):
    repository, git, calls = source
    code = tmp_path / 'code'
    repository_id = store.save(
        'shop', RepositoryInput(remote_url='example/repo', username='private-git-user', token='test-token'), 'admin', ''
    )
    store.enqueue('shop', repository_id, 'admin', '')
    assert repository_worker.run_once(store, code)
    first = store.list('shop')[0]
    assert first['sync_status'] == 'succeeded', first['sync_error']
    first_checkout = code / first['checkout_path']
    assert (first_checkout / 'README.md').read_text() == 'First version\n'
    assert first['current_commit'] == git('rev-parse', 'HEAD')
    assert 'test-token' not in (first_checkout / '.git/config').read_text()
    assert 'private-git-user' not in (first_checkout / '.git/config').read_text()
    assert 'https://github.com/example/repo.git' in (first_checkout / '.git/config').read_text()
    assert not (first_checkout / '.git/objects/info/alternates').exists()
    for command, env in calls:
        assert 'test-token' not in ' '.join(command)
        assert 'private-git-user' not in ' '.join(command)
        assert env['KA_GITHUB_TOKEN'] == 'test-token'
        assert env['KA_GIT_USERNAME'] == 'private-git-user'
        assert env['GIT_CONFIG_GLOBAL'] == os.devnull
        assert 'core.hooksPath=/dev/null' in command
    (repository / 'README.md').write_text('Second version\n')
    git('commit', '-am', 'Second version')
    store.enqueue('shop', repository_id, 'admin', '')
    assert repository_worker.run_once(store, code)
    second = store.list('shop')[0]
    assert second['sync_status'] == 'succeeded', second['sync_error']
    assert second['checkout_path'] != first['checkout_path']
    assert (code / second['checkout_path'] / 'README.md').read_text() == 'Second version\n'
    assert (first_checkout / 'README.md').read_text() == 'First version\n'
    store.save(
        'shop', RepositoryInput(remote_url='example/repo', requested_ref='missing-branch'), 'admin', '', repository_id
    )
    store.enqueue('shop', repository_id, 'admin', '')
    assert repository_worker.run_once(store, code)
    failed = store.list('shop')[0]
    assert failed['sync_status'] == 'failed'
    assert failed['checkout_path'] == second['checkout_path'] and failed['current_commit'] == second['current_commit']
    assert failed['synced_at'] == second['synced_at']
    assert 'test-token' not in failed['sync_error'] and 'private-git-user' not in failed['sync_error']
    assert not list(code.rglob('.pull-*'))
    assert not repository_worker.run_once(store, code)


def test_expired_worker_job_can_be_retried(store, tmp_path):
    repository_id = store.save('shop', RepositoryInput(remote_url='example/repo'), 'admin', '')
    job_id = store.enqueue('shop', repository_id, 'admin', '')
    with store.connect() as conn:
        conn.execute(
            """UPDATE ka.jobs SET status='running', created_at=now()-interval '21 minutes', started_at=now()-interval '20 minutes',
            worker_id='gone', lease_token=gen_random_uuid(), lease_expires_at=now()-interval '1 minute' WHERE id=%s""",
            (job_id,),
        )
    assert not repository_worker.run_once(store, tmp_path)
    assert store.list('shop')[0]['sync_status'] == 'failed'
    assert store.enqueue('shop', repository_id, 'admin', '') != job_id


def test_named_clone_periodic_pull_and_local_change_protection(store, source, tmp_path, monkeypatch):
    repository, git, calls = source
    root = tmp_path / 'code'
    root.mkdir()
    monkeypatch.setenv('CODE_ROOT', str(root))
    repo_id = store.save(
        'shop',
        RepositoryInput(
            name='Named code',
            remote_url='example/repo',
            folder_paths=[],
            clone_path='applications/payments',
            clone_now=True,
            sync_interval_minutes=60,
            username='scheduled-user',
            token='scheduled-token',
        ),
        'admin',
        '',
    )
    assert repository_worker.run_once(store, root)
    saved = store.list('shop')[0]
    assert saved['sync_status'] == 'succeeded', saved['sync_error']
    assert saved['checkout_path'] == 'applications/payments' and saved['folder_paths'] == ['applications/payments']
    checkout = root / saved['checkout_path']
    assert (checkout / 'README.md').read_text() == 'First version\n'
    (repository / 'README.md').write_text('Scheduled version\n')
    git('commit', '-am', 'Scheduled change')
    with store.connect() as conn:
        conn.execute("UPDATE ka.repositories SET next_sync_at=now()-interval '1 minute' WHERE id=%s", (repo_id,))
    assert repository_worker.run_once(store, root)
    updated = store.list('shop')[0]
    assert updated['sync_status'] == 'succeeded', updated['sync_error']
    assert updated['checkout_path'] == saved['checkout_path']
    assert (checkout / 'README.md').read_text() == 'Scheduled version\n'
    assert all(
        env['KA_GIT_USERNAME'] == 'scheduled-user' and env['KA_GITHUB_TOKEN'] == 'scheduled-token' for _, env in calls
    )
    with store.connect() as conn:
        assert conn.execute(
            "SELECT next_sync_at > now()+interval '59 minutes' AS scheduled FROM ka.repositories WHERE id=%s",
            (repo_id,),
        ).fetchone()['scheduled']
    assert not repository_worker.run_once(store, root)
    (checkout / 'README.md').write_text('Local work that must survive\n')
    store.enqueue('shop', repo_id, 'admin', '')
    assert repository_worker.run_once(store, root)
    failed = store.list('shop')[0]
    assert failed['sync_status'] == 'failed' and 'local changes' in failed['sync_error']
    assert failed['current_commit'] == updated['current_commit']
    assert (checkout / 'README.md').read_text() == 'Local work that must survive\n'


def test_wrong_encryption_key_fails_without_publishing_or_exposing_token(store, tmp_path):
    repository_id = store.save(
        'shop', RepositoryInput(remote_url='example/repo', token='private-test-token'), 'admin', ''
    )
    store.enqueue('shop', repository_id, 'admin', '')
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    store.cipher = AESGCM(b'x' * 32)
    assert repository_worker.run_once(store, tmp_path)
    row = store.list('shop')[0]
    assert row['sync_status'] == 'failed' and row['checkout_path'] is None
    assert 'cannot be decrypted' in row['sync_error'] and 'private-test-token' not in row['sync_error']


@pytest.mark.parametrize('username', [None, 'configured-private-user'])
def test_askpass_only_returns_credentials_for_matching_prompt(username):
    script = str(Path(git_askpass.__file__))
    env = {'PATH': os.environ['PATH'], 'KA_GITHUB_TOKEN': 'test-only-password'}
    if username is not None:
        env['KA_GIT_USERNAME'] = username
    for prompt, expected in [
        ('Username for https://github.com', username or 'x-access-token'),
        ('Password for https://github.com', 'test-only-password'),
        ("Password for 'https://username@github.com'", 'test-only-password'),
        ("Username for 'https://git.example/password/repo'", username or 'x-access-token'),
        ('Other prompt', ''),
    ]:
        result = subprocess.run([script, prompt], env=env, check=True, capture_output=True, text=True)
        assert result.stdout.strip() == expected and result.stderr == ''


def test_existing_v1_token_still_clones(store, source, tmp_path):
    _, _, calls = source
    repository_id = store.save('shop', RepositoryInput(remote_url='example/repo', token='legacy-token'), 'admin', '')
    with store.connect() as conn:
        credential = conn.execute('SELECT * FROM ka.credentials').fetchone()
        nonce = os.urandom(12)
        encrypted = nonce + store.cipher.encrypt(
            nonce, b'legacy-token', f'{credential["project_id"]}:{credential["id"]}:git_https_token:v1'.encode()
        )
        conn.execute("UPDATE ka.credentials SET key_version='v1',ciphertext=%s", (encrypted,))
    store.enqueue('shop', repository_id, 'admin', '')
    assert repository_worker.run_once(store, tmp_path / 'code')
    assert store.list('shop')[0]['sync_status'] == 'succeeded'
    assert all(
        env['KA_GIT_USERNAME'] == 'x-access-token' and env['KA_GITHUB_TOKEN'] == 'legacy-token' for _, env in calls
    )
