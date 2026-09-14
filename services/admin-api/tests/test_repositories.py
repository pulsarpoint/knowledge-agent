import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from pydantic import ValidationError

from knowledge_admin.repositories import RepositoryInput, github_url
from test_api import _client

HEADERS = {"X-Forwarded-Groups": "docs-admins", "X-Forwarded-User": "admin-id", "X-Forwarded-Email": "admin@example.test"}
URL = "https://github.com/example/repo.git"


@pytest.mark.parametrize("url", ["Example/Repo", "https://github.com/Example/Repo.git", "https://github.com/example/repo/"])
def test_canonical_github_url(url):
    assert github_url(url) == URL


@pytest.mark.parametrize("url", ["https://github.com@localhost/r/x", "https://token@github.com/r/x", "https://github.com.evil.test/r/x",
    "file:///tmp/repo", "ssh://github.com/r/x", "git@github.com:r/x", "https://github.com/r/x?token=secret",
    "https://github.com/r/x#main", "https://github.com:443/r/x", "https://github.com/r/../x", "https://github.com/r/%2e%2e", "r/.."])
def test_reject_unsafe_remote(url):
    with pytest.raises(ValueError):
        github_url(url)


@pytest.mark.parametrize("ref", ["--upload-pack=evil", "refs/../secrets", "a b", "a\nb", "a//b", "a/.hidden", "main.lock", "a/", "a^{}"])
def test_reject_unsafe_ref(ref):
    with pytest.raises(ValidationError):
        RepositoryInput(remote_url=URL, requested_ref=ref)


def test_api_encrypts_git_credentials_and_scopes_repositories(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post("/api/repositories", headers=HEADERS, json={"remote_url": "Example/Repo", "username": "private-git-user", "token": "github_test_secret"})
    assert response.status_code == 201, response.text
    repository_id = UUID(response.json()["id"])
    listing = client.get("/api/repositories", headers=HEADERS)
    row = listing.json()["repositories"][0]
    assert row["has_token"] is True and row["remote_url"] == URL
    assert row["sync_status"] is None and row["checkout_path"] is None
    assert all(secret not in listing.text + response.text for secret in ("github_test_secret", "private-git-user", "ciphertext", "username"))
    with store.connect() as conn:
        credential = conn.execute("SELECT * FROM ka.credentials").fetchone()
        envelope = bytes(credential["ciphertext"])
        assert b"github_test_secret" not in envelope and b"private-git-user" not in envelope
        assert credential['key_version'] == 'v2'
        aad = f"{credential['project_id']}:{credential['id']}:git_https_token:v2".encode()
        assert json.loads(store.cipher.decrypt(envelope[:12], envelope[12:], aad)) == {'username':'private-git-user', 'token':'github_test_secret'}
        with pytest.raises(InvalidTag):
            store.cipher.decrypt(envelope[:12], envelope[12:], b"different-repository")
        audit = json.dumps(conn.execute("SELECT details FROM ka.audit_events").fetchall())
        assert "github_test_secret" not in audit and "private-git-user" not in audit
    assert store.list("another-app") == []
    with pytest.raises(HTTPException) as error:
        store.enqueue("another-app", repository_id, "admin-id", "")
    assert error.value.status_code == 404
    assert client.get("/api/unknown/repositories", headers=HEADERS).status_code == 404
    duplicate = client.post("/api/repositories", headers=HEADERS, json={"remote_url": URL})
    assert duplicate.status_code == 409


def test_token_edit_preserve_rotate_remove_and_url_change_rolls_back(store):
    repository_id = store.save("shop", RepositoryInput(remote_url=URL, username="original-user", token="original"), "admin", "")
    def credential():
        with store.connect() as conn:
            return conn.execute("SELECT *, id AS credential_id FROM ka.credentials").fetchone()
    first = credential()
    store.save("shop", RepositoryInput(remote_url=URL, name="Renamed", requested_ref="feature/docs"), "admin", "", repository_id)
    assert bytes(credential()["ciphertext"]) == bytes(first["ciphertext"])
    with pytest.raises(HTTPException):
        store.save("shop", RepositoryInput(remote_url="other/repo", token="wrong-token"), "admin", "", repository_id)
    assert bytes(credential()["ciphertext"]) == bytes(first["ciphertext"])
    store.save("shop", RepositoryInput(remote_url=URL, token="replacement"), "admin", "", repository_id)
    assert bytes(credential()["ciphertext"]) != bytes(first["ciphertext"])
    assert store.decrypt_git_credentials(credential()) == ('original-user', 'replacement')
    store.save("shop", RepositoryInput(remote_url=URL, username="replacement-user"), "admin", "", repository_id)
    assert store.decrypt_git_credentials(credential()) == ('replacement-user', 'replacement')
    store.save("shop", RepositoryInput(remote_url=URL, token=None), "admin", "", repository_id)
    assert credential() is None
    assert store.list("shop")[0]["has_token"] is False


def test_legacy_token_can_be_upgraded_without_reentering_it(store):
    repository_id = store.save('shop', RepositoryInput(remote_url=URL, token='legacy-token'), 'admin', '')
    with store.connect() as conn:
        saved = conn.execute('SELECT *, id AS credential_id FROM ka.credentials').fetchone()
        nonce = os.urandom(12)
        envelope = nonce + store.cipher.encrypt(nonce, b'legacy-token',
            f"{saved['project_id']}:{saved['id']}:git_https_token:v1".encode())
        conn.execute("UPDATE ka.credentials SET key_version='v1',ciphertext=%s", (envelope,))
    store.save('shop', RepositoryInput(remote_url=URL, username='configured-user'), 'admin', '', repository_id)
    with store.connect() as conn:
        upgraded = conn.execute('SELECT *, id AS credential_id FROM ka.credentials').fetchone()
    assert upgraded['key_version'] == 'v2'
    assert store.decrypt_git_credentials(upgraded) == ('configured-user', 'legacy-token')


def test_new_username_requires_token_and_damaged_credentials_can_be_replaced(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/repositories', headers=HEADERS, json={'remote_url':URL,'username':'private-user'})
    assert response.status_code == 400 and 'private-user' not in response.text
    response = client.post('/api/repositories', headers=HEADERS, json={'remote_url':URL,'username':'private-user','token':'original'})
    repository_id = response.json()['id']
    with store.connect() as conn:
        conn.execute('UPDATE ka.credentials SET ciphertext=%s', (os.urandom(40),))
    response = client.put(f'/api/repositories/{repository_id}', headers=HEADERS, json={'remote_url':URL,'token':'replacement'})
    assert response.status_code == 400 and 'cannot be decrypted' in response.text and 'replacement' not in response.text
    response = client.put(f'/api/repositories/{repository_id}', headers=HEADERS, json={'remote_url':URL,'username':'new-user','token':'replacement'})
    assert response.status_code == 200, response.text
    with store.connect() as conn:
        saved = conn.execute('SELECT *, id AS credential_id FROM ka.credentials').fetchone()
    assert store.decrypt_git_credentials(saved) == ('new-user', 'replacement')


def test_concurrent_pull_is_idempotent_and_blocks_edits(store):
    repository_id = store.save("shop", RepositoryInput(remote_url=URL), "admin", "")
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: store.enqueue("shop", repository_id, "admin", ""), range(4)))
    assert len(set(jobs)) == 1
    row = store.list("shop")[0]
    assert row["sync_status"] == "queued" and row["job_id"] == jobs[0]
    with pytest.raises(HTTPException) as error:
        store.save("shop", RepositoryInput(remote_url=URL, token="new"), "admin", "", repository_id)
    assert error.value.status_code == 409


@pytest.mark.parametrize("method,path", [("GET", "/api/repositories"), ("POST", "/api/repositories"),
    ("PUT", f"/api/repositories/{uuid4()}"), ("POST", f"/api/repositories/{uuid4()}/pull")])
def test_repository_endpoints_require_admin_before_database(tmp_path, method, path):
    response = _client(tmp_path).request(method, path, json={"remote_url": URL}, headers={"X-Forwarded-Groups": "readers"})
    assert response.status_code == 403


def test_mutations_require_identity_and_do_not_reflect_invalid_tokens(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.post("/api/repositories", headers={"X-Forwarded-Groups": "docs-admins"}, json={"remote_url": URL}).status_code == 403
    response = client.post("/api/repositories", headers=HEADERS, json={"remote_url": URL, "token": "secret with spaces"})
    assert response.status_code == 400 and "secret with spaces" not in response.text
    response = client.post("/api/repositories", headers=HEADERS, json={"remote_url": "https://embedded-secret@github.com/owner/repo", "token": "other-secret"})
    assert response.status_code == 400 and "embedded-secret" not in response.text and "other-secret" not in response.text
    response = client.post('/api/repositories', headers=HEADERS, json={'remote_url':URL, 'username':'private:user', 'token':'other-secret'})
    assert response.status_code == 400 and 'private:user' not in response.text and 'other-secret' not in response.text


def test_missing_storage_has_actionable_error(tmp_path):
    response = _client(tmp_path).get("/api/repositories", headers=HEADERS)
    assert response.status_code == 503 and "not configured" in response.text
