"""Sync worker: one collection indexed in place with patterns, skips and its own revision."""
import os

import pytest
from test_workspace import chunks, collection, configure, search, sync, update, write

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
    hits = search(env, "MAX_RETRIES", ["payments-code"]).json()["results"]
    assert hits[0]["path"] == "code/payments/src/retry.py" and hits[0]["collection"] == "payments-code"
    assert chunks(env, created, "code/payments/src/retry.py") == ["MAX_RETRIES = 5  # payment retries"]
    assert chunks(env, created, "code/payments/tests/test_retry.py") == []


def test_sync_skips_links_binaries_and_counts_them(env):
    roots = env[6]
    folder = roots["jira"] / "docs"
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
    roots = env[6]
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    write(env, "a.md", "# A\n\nalpha")
    write(env, "b.txt", "beta")
    _, row = sync(env, created)
    assert row["indexed_files"] == 2
    (roots["jira"] / "docs" / "a.md").unlink()
    update(env, created, include=["**/*.md"])
    stats, row = sync(env, created)
    assert stats["removed_entries"] == 2 and row["indexed_files"] == 0 and row["status"] == "up_to_date"


def test_unchanged_files_are_skipped_and_vectors_reused(env):
    roots = env[6]
    (roots["jira"] / "docs").mkdir()
    configure(env)  # selects the fixture embedding model
    created = collection(env, embeddings_enabled=True, search_mode="hybrid")
    write(env)
    first, row = sync(env, created)
    assert first["embedded_chunks"] >= 1 and row["embedded_chunks"] == row["chunks"]
    second, _ = sync(env, created)
    assert second["skipped_files"] == 1 and second["embedded_chunks"] == 0
    other = collection(env, "copy", "jira/docs", embeddings_enabled=True)
    third, _ = sync(env, other)
    assert third["embedded_chunks"] == 0 and third["reused_vectors"] >= 1


def test_embeddings_need_a_model(env):
    roots = env[6]
    (roots["jira"] / "docs").mkdir()
    created = collection(env, embeddings_enabled=True)
    write(env)
    stats, row = sync(env, created)
    assert stats is None and row["status"] == "failed" and "embedding model" in row["job"]["error"]


def test_editing_another_collection_does_not_fail_this_sync(env):
    workspace, client, _, prefix, _, transport, roots = env
    for name in ("a", "b"):
        (roots["jira"] / name).mkdir()
    first = collection(env, "a", "jira/a")
    second = collection(env, "b", "jira/b")
    write(env, "x.md", "# X", name="a")
    assert client.post(prefix + f"/collections/{first}/sync", json={}).status_code == 202
    job = claim(workspace)
    update(env, second, description="changed")
    assert process(workspace, job, transport) is not None


def test_settings_change_during_sync_fails_that_sync(env):
    workspace, client, _, prefix, state, transport, roots = env
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    write(env)
    assert client.post(prefix + f"/collections/{created}/sync", json={}).status_code == 202
    job = claim(workspace)
    update(env, created, chunk_size=1000)
    assert process(workspace, job, transport) is None
    assert "Settings changed" in client.get(prefix + f"/collections/{created}").json()["job"]["error"]


def test_unchanged_files_are_not_parsed_and_deletions_are_published(env, monkeypatch):
    roots = env[6]
    (roots["jira"] / "docs").mkdir()
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
    (roots["jira"] / "docs" / "other.txt").unlink()
    third, row = sync(env, created)
    assert third["parsed_files"] == 1 and third["removed_entries"] == 1 and row["indexed_files"] == 1
    assert chunks(env, created, "jira/docs/other.txt") == []
    assert "new policy" in chunks(env, created)[0]


def test_force_reparses_and_markdown_title_is_indexed(env):
    roots = env[6]
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    write(env, content="# UniqueTitleNeedle\n\n## Section\n\nOrdinary body text.")
    sync(env, created)
    stats, _ = sync(env, created, force=True)
    assert stats["parsed_files"] == 1
    assert all(text.startswith("UniqueTitleNeedle") for text in chunks(env, created))


def test_json_is_pretty_printed_and_large_files_are_skipped(env):
    client, prefix, roots = env[1], env[3], env[6]
    folder = roots["jira"] / "docs"
    folder.mkdir()
    (folder / "data.json").write_text('{"retries":5}')
    (folder / "big.md").write_text("x" * 600)
    config = client.get(prefix + "/settings").json()
    assert client.put(prefix + "/settings", json={"revision": config["revision"], "settings": {"max_file_bytes": 500}}).status_code == 200
    created = collection(env, include=["**/*.json", "**/*.md"])
    stats, row = sync(env, created)
    assert stats["skipped_large"] == 1 and row["indexed_files"] == 1
    assert chunks(env, created, "jira/docs/data.json") == ['{\n  "retries": 5\n}']


def test_model_change_reembeds_without_parsing_and_failure_publishes_nothing(env):
    roots, state = env[6], env[4]
    (roots["jira"] / "docs").mkdir()
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
    (roots["jira"] / "docs").mkdir()
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
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    configure(env)
    update(env, created, embeddings_enabled=True)
    state["hook"] = lambda: (roots["jira"] / "docs" / "readme.md").write_text("Changed during processing")
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
    (roots["jira"] / "docs").mkdir()
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
        (roots["jira"] / name).mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True)
    kept = collection(env, "keep", "jira/keep", embeddings_enabled=True)
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
    assert (roots["jira"] / "docs" / "unique.txt").is_file()


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
    (roots["jira"] / "docs").mkdir()
    created = collection(env, embeddings_enabled=True, search_mode="embedding")
    write(env)
    assert client.post(prefix + '/test/embedding').status_code == 200
    stats, row = sync(env, created)
    assert row['status'] == 'up_to_date' and stats['embedded_chunks'] >= 1 and row['embedded_chunks'] == row['chunks']
    assert all(call['dimensions'] == 256 and call['normalize'] for call in state['calls'])
    state['calls'].clear()
    stats, _ = sync(env, created)
    assert stats['parsed_files'] == stats['embedded_chunks'] == 0 and not state['calls']


def test_nul_anywhere_marks_a_file_binary(env):
    roots = env[6]
    folder = roots["jira"] / "docs"
    folder.mkdir()
    (folder / "ok.md").write_text("# OK")
    (folder / "late.md").write_bytes(b"a" * 9000 + b"\x00tail")
    created = collection(env)
    stats, row = sync(env, created)
    assert stats["skipped_binary"] == 1 and row["indexed_files"] == 1 and row["status"] == "up_to_date"


def test_reused_vector_removed_before_publish_fails_the_sync(env):
    client, prefix, state, roots = env[1], env[3], env[4], env[6]
    for name in ("a", "b"):
        (roots["jira"] / name).mkdir()
    configure(env)
    first = collection(env, "a", "jira/a", embeddings_enabled=True)
    second = collection(env, "b", "jira/b", embeddings_enabled=True)
    write(env, "a.md", "sharedembeddingneedle", name="a")
    sync(env, first)
    write(env, "a.md", "sharedembeddingneedle", name="b")  # reuses the vector from collection a
    write(env, "z.md", "fresh text needs an embedding", name="b")
    revision = client.get(prefix + f"/collections/{first}").json()["revision"]

    def delete_first():  # runs during b's embedding phase, after a.md counted its vector as reused
        state["hook"] = None
        assert client.delete(prefix + f"/collections/{first}", params={"revision": revision, "confirmed": True}).status_code == 200
    state["hook"] = delete_first
    stats, row = sync(env, second)
    assert stats is None and "Embeddings changed" in row["job"]["error"]
    stats, row = sync(env, second)
    assert stats["embedded_chunks"] >= 1 and row["embedded_chunks"] == row["chunks"] and row["status"] == "up_to_date"


def test_sync_indexes_two_paths_under_full_paths(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["code"] / "payments" / "src").mkdir(parents=True)
    (roots["code"] / "payments" / "src" / "retry.py").write_text("MAX_RETRIES = 5  # payment retries\n")
    (roots["jira"] / "jira").mkdir()
    (roots["jira"] / "jira" / "PAY-1.md").write_text("# PAY-1\n\nRetries fail on timeout.")
    created = collection(env, "payments", "code/payments", "jira/jira", include=["**/*.py", "**/*.md"])
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 2 and row["indexed_files"] == 2 and row["status"] == "up_to_date"
    files = client.get(prefix + f"/collections/{created}/files").json()["files"]
    assert [(f["path"], f["source"]) for f in files] == [
        ("code/payments/src/retry.py", "code/payments"), ("jira/jira/PAY-1.md", "jira/jira")]
    assert {p["source_path"]: p["files"] for p in row["paths"]} == {"code/payments": 1, "jira/jira": 1}
    assert chunks(env, created, "code/payments/src/retry.py") == ["MAX_RETRIES = 5  # payment retries"]


def test_missing_path_fails_sync_and_keeps_index(env):
    workspace, client, _, prefix, _, transport, roots = env
    for name in ("a", "b"):
        (roots["jira"] / name).mkdir()
        (roots["jira"] / name / "x.md").write_text(f"# {name}\n\nretries {name}")
    created = collection(env, "both", "jira/a", "jira/b")
    sync(env, created)
    (roots["jira"] / "b" / "x.md").unlink()
    (roots["jira"] / "b").rmdir()
    row = client.get(prefix + f"/collections/{created}").json()
    assert row["status"] == "folder_missing" and row["missing_paths"] == ["jira/b"]
    refused = client.post(prefix + f"/collections/{created}/sync", json={})
    assert refused.status_code == 409 and "Path jira/b is missing" in refused.json()["error"]["message"]
    # A sync queued while the folder existed fails in the worker and publishes nothing.
    (roots["jira"] / "b").mkdir()
    assert client.post(prefix + f"/collections/{created}/sync", json={}).status_code == 202
    job = claim(workspace)
    (roots["jira"] / "b").rmdir()
    assert process(workspace, job, transport) is None
    row = client.get(prefix + f"/collections/{created}").json()
    assert "Path jira/b is missing" in row["job"]["error"] and row["indexed_files"] == 2
    hits = search(env, "retries", ["both"]).json()["results"]
    assert {hit["path"] for hit in hits} == {"jira/a/x.md", "jira/b/x.md"}


def test_removing_a_path_drops_its_files_from_search(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("a", "b"):
        (roots["jira"] / name).mkdir()
        (roots["jira"] / name / "x.md").write_text(f"# {name}\n\nretries {name}")
    created = collection(env, "both", "jira/a", "jira/b")
    sync(env, created)
    path_id = next(p["id"] for p in client.get(prefix + f"/collections/{created}").json()["paths"]
                   if p["source_path"] == "jira/b")
    removed = client.delete(prefix + f"/collections/{created}/paths/{path_id}")
    assert removed.status_code == 200 and [p["source_path"] for p in removed.json()["paths"]] == ["jira/a"]
    assert {hit["path"] for hit in search(env, "retries", ["both"]).json()["results"]} == {"jira/a/x.md"}
    row = client.get(prefix + f"/collections/{created}").json()
    assert row["indexed_files"] == 1 and row["status"] == "needs_refresh"
    assert (roots["jira"] / "b" / "x.md").is_file()  # files on disk are never deleted
