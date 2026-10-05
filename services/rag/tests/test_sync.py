"""Sync worker: one collection indexed in place with patterns, skips and its own revision."""
import os

import pytest
from test_workspace import chunks, collection, configure, sync, write

from rag.workspace_worker import claim, process

# env and workspace_postgres come from conftest.py


def test_sync_indexes_code_as_plain_text_with_patterns(env):
    roots = env[6]
    repo = roots["code"] / "payments"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "retry.py").write_text("MAX_RETRIES = 5  # payment retries\n")
    (repo / "tests" / "test_retry.py").write_text("assert MAX_RETRIES\n")
    created = collection(env, "payments-code", "code/payments", include=["**/*.py"], exclude=["tests/**"])
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 1 and row["indexed_files"] == 1 and row["status"] == "up_to_date"
    # Task 5 (search by collection) adds: search(env, "MAX_RETRIES", ["payments-code"]) hits src/retry.py.
    assert chunks(env, created, "src/retry.py") == ["MAX_RETRIES = 5  # payment retries"]
    assert chunks(env, created, "tests/test_retry.py") == []


def test_sync_skips_links_binaries_and_counts_them(env):
    roots = env[6]
    folder = roots["rag-data"] / "docs"
    folder.mkdir()
    (folder / "ok.md").write_text("# OK\n\nRetries.")
    (folder / "bin.md").write_bytes(b"\x00\x01binary")
    (folder / "latin.md").write_bytes("caf\xe9".encode("latin-1"))
    os.symlink(folder / "ok.md", folder / "link.md")
    created = collection(env)
    stats, row = sync(env, created)
    assert row["indexed_files"] == 1
    assert stats["skipped_binary"] == 2 and stats["skipped_links"] == 1


def test_sync_removes_deleted_and_unmatched_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env, "a.md", "# A\n\nalpha")
    write(env, "b.txt", "beta")
    _, row = sync(env, created)
    assert row["indexed_files"] == 2
    (roots["rag-data"] / "docs" / "a.md").unlink()
    client.put(prefix + f"/collections/{created}", json={"name": "docs", "source_path": "rag-data/docs",
                                                        "include": ["**/*.md"], "revision": row["revision"]})
    stats, row = sync(env, created)
    assert stats["removed_entries"] == 2 and row["indexed_files"] == 0 and row["status"] == "up_to_date"


def test_unchanged_files_are_skipped_and_vectors_reused(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    configure(env)  # selects the fixture embedding model
    created = collection(env, embeddings_enabled=True, search_mode="hybrid")
    write(env)
    first, row = sync(env, created)
    assert first["embedded_chunks"] >= 1 and row["embedded_chunks"] == row["chunks"]
    second, _ = sync(env, created)
    assert second["skipped_files"] == 1 and second["embedded_chunks"] == 0
    other = collection(env, "copy", "rag-data/docs", embeddings_enabled=True)
    third, _ = sync(env, other)
    assert third["embedded_chunks"] == 0 and third["reused_vectors"] >= 1


def test_embeddings_need_a_model(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env, embeddings_enabled=True)
    write(env)
    stats, row = sync(env, created)
    assert stats is None and row["status"] == "failed" and "embedding model" in row["job"]["error"]


def test_editing_another_collection_does_not_fail_this_sync(env):
    workspace, client, _, prefix, _, transport, roots = env
    for name in ("a", "b"):
        (roots["rag-data"] / name).mkdir()
    first = collection(env, "a", "rag-data/a")
    second = collection(env, "b", "rag-data/b")
    write(env, "x.md", "# X", name="a")
    assert client.post(prefix + f"/collections/{first}/sync", json={}).status_code == 202
    job = claim(workspace)
    row_b = client.get(prefix + f"/collections/{second}").json()
    client.put(prefix + f"/collections/{second}", json={"name": "b", "source_path": "rag-data/b", "description": "changed", "revision": row_b["revision"]})
    assert process(workspace, job, transport) is not None


def test_settings_change_during_sync_fails_that_sync(env):
    workspace, client, _, prefix, state, transport, roots = env
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    assert client.post(prefix + f"/collections/{created}/sync", json={}).status_code == 202
    job = claim(workspace)
    row = client.get(prefix + f"/collections/{created}").json()
    client.put(prefix + f"/collections/{created}", json={"name": "docs", "source_path": "rag-data/docs", "chunk_size": 1000, "revision": row["revision"]})
    assert process(workspace, job, transport) is None
    assert "Settings changed" in client.get(prefix + f"/collections/{created}").json()["job"]["error"]


def test_unchanged_files_are_not_parsed_and_deletions_are_published(env, monkeypatch):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    write(env, "other.txt", "Another original document")
    first, _ = sync(env, created)
    assert first["parsed_files"] == 2 and first["hashed_files"] == 2
    from rag import workspace_worker
    parser = workspace_worker.parse_source
    monkeypatch.setattr(workspace_worker, "parse_source", lambda *a: pytest.fail("Unchanged files must not be parsed"))
    second, _ = sync(env, created)
    assert second["skipped_files"] == 2 and second["parsed_files"] == 0
    monkeypatch.setattr(workspace_worker, "parse_source", parser)
    write(env, content="# Payments\n\nRetries have a new policy.")
    document = client.get(prefix + "/file", params={"collection": "docs", "path": "other.txt"}).json()
    assert client.delete(prefix + "/file", params={"collection": "docs", "path": "other.txt", "expected_hash": document["hash"]}).status_code == 200
    third, row = sync(env, created)
    assert third["parsed_files"] == 1 and third["removed_entries"] == 1 and row["indexed_files"] == 1
    assert chunks(env, created, "other.txt") == []
    assert "new policy" in chunks(env, created)[0]


def test_force_reparses_and_markdown_title_is_indexed(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env, content="# UniqueTitleNeedle\n\n## Section\n\nOrdinary body text.")
    sync(env, created)
    stats, _ = sync(env, created, force=True)
    assert stats["parsed_files"] == 1
    assert all(text.startswith("UniqueTitleNeedle") for text in chunks(env, created))


def test_json_is_pretty_printed_and_large_files_are_skipped(env):
    client, prefix, roots = env[1], env[3], env[6]
    folder = roots["rag-data"] / "docs"
    folder.mkdir()
    (folder / "data.json").write_text('{"retries":5}')
    (folder / "big.md").write_text("x" * 600)
    config = client.get(prefix + "/settings").json()
    assert client.put(prefix + "/settings", json={"revision": config["revision"], "settings": {"max_file_bytes": 500}}).status_code == 200
    created = collection(env, include=["**/*.json", "**/*.md"])
    stats, row = sync(env, created)
    assert stats["skipped_large"] == 1 and row["indexed_files"] == 1
    assert chunks(env, created, "data.json") == ['{\n  "retries": 5\n}']


def test_model_change_reembeds_without_parsing_and_failure_publishes_nothing(env):
    roots, state = env[6], env[4]
    (roots["rag-data"] / "docs").mkdir()
    configure(env, embedding_model="test", embedding_dimensions=3)
    created = collection(env, embeddings_enabled=True, search_mode="hybrid")
    write(env)
    stats, row = sync(env, created)
    assert stats["embedded_chunks"] >= 1 and row["embedded_chunks"] == row["chunks"]
    state["calls"].clear()
    stats, _ = sync(env, created)
    assert stats["embedded_chunks"] == 0 and state["calls"] == []
    write(env, "copy.md")
    stats, _ = sync(env, created)
    assert stats["parsed_files"] == 1 and stats["embedded_chunks"] == 0 and stats["reused_vectors"] > 0
    configure(env, embedding_version="2")
    stats, _ = sync(env, created)
    assert stats["parsed_files"] == 0 and stats["embedded_chunks"] > 0
    write(env, content="# Changed\n\nReplacementneedle must not publish on failure.")
    state["fail"] = True
    stats, row = sync(env, created)
    assert stats is None and row["status"] == "failed" and row["job"]["status"] == "failed"
    assert "SECRET" not in row["job"]["error"]
    assert not any("Replacementneedle" in text for text in chunks(env, created))
    state["fail"] = False
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 1 and row["status"] == "up_to_date"
    assert any("Replacementneedle" in text for text in chunks(env, created))


def test_model_edit_during_sync_cannot_publish_stale_vectors(env):
    workspace, project, state, roots = env[0], env[2], env[4], env[6]
    (roots["rag-data"] / "docs").mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True)
    write(env)

    def edit_model():
        with workspace.connect() as conn:
            conn.execute("UPDATE ka.llm_models SET embedding_version='2' WHERE id=%s", (project,))
    state['hook'] = edit_model
    stats, row = sync(env, created)
    assert stats is None and row['job']['status'] == 'failed' and "Settings changed" in row['job']['error']
    assert row['indexed_files'] == 0


def test_cancel_and_external_edit_cannot_publish(env):
    workspace, client, prefix, state, transport, roots = env[0], env[1], env[3], env[4], env[5], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    configure(env)
    client.put(prefix + f"/collections/{created}", json={"name": "docs", "source_path": "rag-data/docs", "embeddings_enabled": True,
                                                        "revision": client.get(prefix + f"/collections/{created}").json()["revision"]})
    state["hook"] = lambda: (roots["rag-data"] / "docs" / "readme.md").write_text("Changed during processing")
    stats, row = sync(env, created)
    assert stats is None and "Files changed" in row["job"]["error"]
    assert any("exponential backoff" in text for text in chunks(env, created))
    state["hook"] = None
    queued = client.post(prefix + f"/collections/{created}/sync", json={}).json()
    job = claim(workspace)
    assert client.post(prefix + f"/jobs/{queued['id']}/cancel").status_code == 200
    assert process(workspace, job, transport) is None
    assert client.get(prefix + f"/collections/{created}").json()["job"]["status"] == "cancelled"
    assert any("exponential backoff" in text for text in chunks(env, created))


def test_bad_embedding_dimensions_fail_the_sync(env):
    state, roots = env[4], env[6]
    (roots["rag-data"] / "docs").mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True)
    write(env)
    state["bad_dimension"] = True
    stats, row = sync(env, created)
    assert stats is None and "dimensions" in row["job"]["error"]
    state["bad_dimension"] = False
    stats, row = sync(env, created)
    assert stats is not None and row["status"] == "up_to_date"


@pytest.mark.parametrize("phase", ["queued", "running", "embedding"])
def test_deleting_a_collection_cancels_its_sync_and_keeps_shared_vectors(env, phase):
    workspace, client, project, prefix, state, transport, roots = env
    for name in ("docs", "keep"):
        (roots["rag-data"] / name).mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True)
    kept = collection(env, "keep", "rag-data/keep", embeddings_enabled=True)
    write(env, "shared.txt", "sharedembeddingneedle")
    write(env, "unique.txt", "deleteduniqueneedle")
    write(env, "shared.txt", "sharedembeddingneedle", name="keep")
    sync(env, kept)
    revision = client.get(prefix + f"/collections/{created}").json()["revision"]
    delete = lambda: client.delete(prefix + f"/collections/{created}", params={"revision": revision, "confirmed": True})
    queued = client.post(prefix + f"/collections/{created}/sync", json={}).json()
    if phase == "queued":
        assert delete().status_code == 200
        assert claim(workspace) is None
    else:
        job = claim(workspace)
        if phase == "running":
            assert delete().status_code == 200
        else:
            def remove_during_request():
                state["hook"] = None
                assert delete().status_code == 200
            state["hook"] = remove_during_request
        assert process(workspace, job, transport) is None
    recorded = next(j for j in client.get(prefix + "/jobs").json()["jobs"] if j["id"] == queued["id"])
    assert recorded["status"] == "cancelled" and recorded["collection"] == "docs"
    with workspace.connect() as conn:
        assert conn.execute("SELECT 1 FROM ka.rag_files WHERE collection_id=%s", (created,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM ka.rag_chunks WHERE text='deleteduniqueneedle'").fetchone() is None
        hashes = {r["input_hash"] for r in conn.execute("SELECT input_hash FROM ka.rag_vectors WHERE project_id=%s", (project,)).fetchall()}
        kept_hashes = {r["input_hash"] for r in conn.execute("SELECT c.input_hash FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id WHERE f.collection_id=%s", (kept,)).fetchall()}
        assert hashes == kept_hashes and hashes
    assert (roots["rag-data"] / "docs" / "unique.txt").is_file()


def test_bedrock_embedding_sync_reuse_and_region_profile(env):
    from dataclasses import replace

    from rag.workspace_models import embedding_profile
    workspace, client, project, prefix, state, _, roots = env
    with workspace.connect() as conn:
        conn.execute("""UPDATE ka.llm_models SET provider='bedrock',model='amazon.titan-embed-text-v2:0',
            base_url=NULL,aws_region='eu-central-1',embedding_dimensions=256 WHERE id=%s""", (project,))
        model = workspace.embedding_model(conn, project)
        assert embedding_profile(model) != embedding_profile(replace(model, aws_region='us-east-1'))
        assert embedding_profile(model) != embedding_profile(replace(model, embedding_dimensions=512))
    configure(env)
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env, embeddings_enabled=True, search_mode="embedding")
    write(env)
    assert client.post(prefix + '/test/embedding').status_code == 200
    stats, row = sync(env, created)
    assert row['status'] == 'up_to_date' and stats['embedded_chunks'] >= 1 and row['embedded_chunks'] == row['chunks']
    assert all(call['dimensions'] == 256 and call['normalize'] for call in state['calls'])
    state['calls'].clear()
    stats, _ = sync(env, created)
    assert stats['parsed_files'] == stats['embedded_chunks'] == 0 and not state['calls']
