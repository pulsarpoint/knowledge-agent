"""Collections API: validation, freshness, sync queue, read-only areas and deletion."""
import time

from test_workspace import collection, sync, update, write

# env and workspace_postgres come from conftest.py


def status(env, collection_id):
    return env[1].get(env[3] + f"/collections/{collection_id}").json()


def test_missing_folder_is_reported(env):
    absent = env[6]["docs"] / "absent"
    absent.mkdir()  # Add folder never creates folders
    created = collection(env, "absent", "docs/absent")
    absent.rmdir()
    row = status(env, created)
    assert row["status"] == "folder_missing" and row["folder_present"] is False
    assert row["missing_paths"] == ["docs/absent"]
    result = env[1].post(env[3] + f"/collections/{created}/sync", json={})
    assert result.status_code == 409 and "missing" in result.json()["error"]["message"]


def test_sync_marks_up_to_date_and_file_edit_marks_stale(env):
    roots = env[6]
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    assert row["status"] == "up_to_date" and row["indexed_files"] == 1 and row["chunks"] >= 1
    time.sleep(0.01)
    write(env, content="# Payments\n\nRetries stop after five attempts.")
    assert status(env, created)["status"] == "needs_refresh"


def test_deleting_a_file_marks_collection_stale(env):
    roots = env[6]
    folder = roots["jira"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, "a.md", "# A")
    write(env, "b.md", "# B")
    sync(env, created)
    assert status(env, created)["status"] == "up_to_date"
    (folder / "b.md").unlink()
    assert status(env, created)["status"] == "needs_refresh"


def test_settings_change_marks_collection_stale(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    updated = update(env, created, include=["**/*.txt"])
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "needs_refresh"
    stale = client.put(prefix + f"/collections/{created}", json={"revision": row["revision"]})
    assert stale.status_code == 409


def test_one_active_sync_per_collection_and_sync_stale(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("a", "b"):
        (roots["jira"] / name).mkdir()
        collection(env, name, f"jira/{name}")
    (roots["docs"] / "c").mkdir()
    first = collection(env, "c", "docs/c")
    (roots["docs"] / "c").rmdir()
    ids = {r["name"]: r["id"] for r in client.get(prefix + "/collections").json()["collections"]}
    assert client.post(prefix + f"/collections/{ids['a']}/sync", json={}).status_code == 202
    assert client.post(prefix + f"/collections/{ids['a']}/sync", json={}).status_code == 409
    assert client.post(prefix + f"/collections/{ids['b']}/sync", json={}).status_code == 202
    queued = client.post(prefix + "/collections/sync-stale").json()["queued"]
    assert queued == []  # a and b are already queued; c's folder is missing, so it is not queued
    assert status(env, first)["status"] == "folder_missing"


def test_delete_keeps_files_and_removes_index(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    assert client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"]}).status_code == 400
    result = client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"], "confirmed": True})
    assert result.status_code == 200 and result.json()["deleted_files"] == 1
    assert (roots["jira"] / "docs" / "readme.md").exists()
    assert client.get(prefix + "/collections").json()["collections"] == []
    assert collection(env) != created  # the name can be reused


def test_stale_or_cross_project_deletion_cannot_remove_data(env):
    workspace, client, prefix, roots = env[0], env[1], env[3], env[6]
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    assert client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"] + 1, "confirmed": True}).status_code == 409
    assert client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"], "confirmed": True},
                         headers={"x-rag-key": "wrong"}).status_code == 403
    with workspace.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other','Other') RETURNING id").fetchone()["id"]
    assert client.delete(f"/backoffice/projects/{other}/collections/{created}",
                         params={"revision": row["revision"], "confirmed": True}).status_code == 404
    assert status(env, created)["indexed_files"] == 1


def test_files_listing_shows_stale_and_removed_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    folder = roots["jira"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, "a.md", "# A\n\nalpha")
    write(env, "b.md", "# B\n\nbeta")
    sync(env, created)
    (folder / "b.md").unlink()
    write(env, "c.md", "# C")
    listing = client.get(prefix + f"/collections/{created}/files").json()
    files = {f["path"]: f for f in listing["files"]}
    assert set(files) == {"jira/docs/a.md", "jira/docs/b.md", "jira/docs/c.md"} and listing["truncated"] is False
    assert files["jira/docs/a.md"]["stale"] is False and files["jira/docs/a.md"]["chunks"] >= 1
    assert files["jira/docs/b.md"]["stale"] is True and files["jira/docs/b.md"]["size"] is None
    assert files["jira/docs/b.md"]["source"] is None  # gone from disk
    assert files["jira/docs/c.md"]["stale"] is True and files["jira/docs/c.md"]["indexed_at"] is None
    texts = client.get(prefix + f"/collections/{created}/chunks", params={"path": "jira/docs/a.md"}).json()["chunks"]
    assert texts and texts[0]["position"] == 0 and "alpha" in texts[0]["text"] and texts[0]["embedded"] is False


def test_jobs_list_and_cancel(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["jira"] / "docs").mkdir()
    created = collection(env)
    queued = client.post(prefix + f"/collections/{created}/sync", json={"force": True}).json()
    jobs = client.get(prefix + "/jobs").json()["jobs"]
    assert [(j["id"], j["collection"], j["collection_id"], j["status"], j["input"]) for j in jobs] == [
        (queued["id"], "docs", created, "queued", {"force": True})]
    assert status(env, created)["status"] == "syncing"
    assert client.post(prefix + f"/jobs/{queued['id']}/cancel").status_code == 200
    assert client.post(prefix + f"/jobs/{queued['id']}/cancel").status_code == 409
    assert client.post(prefix + f"/collections/{created}/sync", json={}).status_code == 202


def test_list_without_status_skips_the_walk(env, monkeypatch):
    workspace, client, prefix, roots = env[0], env[1], env[3], env[6]
    created = collection(env)
    write(env)
    sync(env, created)
    (roots["docs"] / "absent").mkdir()  # Add folder never creates folders
    collection(env, "absent", "docs/absent")
    (roots["docs"] / "absent").rmdir()

    def no_walk(*args, **kwargs):
        raise AssertionError("status=false must not walk folders")
    monkeypatch.setattr(workspace.data_root, "walk", no_walk)
    rows = {r["name"]: r for r in client.get(prefix + "/collections", params={"status": "false"}).json()["collections"]}
    assert rows["docs"]["folder_present"] is True and rows["docs"]["indexed_files"] == 1 and rows["docs"]["chunks"] >= 1
    assert rows["docs"]["embedded_chunks"] == 0 and [p["source_path"] for p in rows["docs"]["paths"]] == ["jira/docs"]
    assert rows["absent"]["folder_present"] is False and "status" not in rows["docs"]
    assert rows["absent"]["missing_paths"] == ["docs/absent"]


def test_collection_with_unsynced_revision_needs_refresh(env):
    workspace, roots = env[0], env[6]
    created = collection(env)
    write(env)
    sync(env, created)
    assert status(env, created)["status"] == "up_to_date"
    with workspace.connect() as conn:  # what migration 0063 leaves for migrated folders
        conn.execute("UPDATE ka.rag_collections SET synced_revision=NULL WHERE id=%s", (created,))
    row = status(env, created)
    assert row["status"] == "needs_refresh" and row["last_synced_at"] is not None


def seed_jobs(env):
    """Twelve finished jobs across two collections, newest last, plus one queued."""
    workspace, project = env[0], env[2]
    ids = {name: collection(env, name, f"jira/{name}") for name in ("jira", "docs")}
    with workspace.connect() as conn:
        for i in range(12):
            name = "jira" if i % 2 else "docs"
            failed = i % 4 == 3
            conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input,status,error,created_at,started_at,finished_at)
                VALUES (%s,'rag_collection_sync',%s,'{}',%s,%s,now() - make_interval(mins => %s),now() - make_interval(mins => %s),now() - make_interval(mins => %s))""",
                (project, ids[name], "failed" if failed else "succeeded", "Embedding model rejected the token" if failed else None,
                 100 - i, 100 - i, 99 - i))
    return ids


def test_jobs_are_paged_newest_first_with_total(env):
    client, prefix = env[1], env[3]
    seed_jobs(env)
    page = client.get(prefix + "/jobs", params={"limit": 5}).json()
    assert page["total"] == 12 and len(page["jobs"]) == 5
    second = client.get(prefix + "/jobs", params={"limit": 5, "offset": 10}).json()
    assert second["total"] == 12 and len(second["jobs"]) == 2
    created = [j["created_at"] for j in page["jobs"]]
    assert created == sorted(created, reverse=True)
    assert client.get(prefix + "/jobs").json()["total"] == 12  # default page still reports the total
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "exploded"}):
        assert client.get(prefix + "/jobs", params=bad).status_code == 400, bad


def test_jobs_filter_by_status_collection_and_text(env):
    client, prefix = env[1], env[3]
    seed_jobs(env)
    failed = client.get(prefix + "/jobs", params={"status": "failed"}).json()
    assert failed["total"] == 3 and {j["status"] for j in failed["jobs"]} == {"failed"}
    jira = client.get(prefix + "/jobs", params={"collection": "jira"}).json()
    assert jira["total"] == 6 and {j["collection"] for j in jira["jobs"]} == {"jira"}
    both = client.get(prefix + "/jobs", params={"collection": "jira", "status": "failed"}).json()
    assert both["total"] == 3
    text = client.get(prefix + "/jobs", params={"q": "token"}).json()
    assert text["total"] == 3
    assert client.get(prefix + "/jobs", params={"q": "DOC"}).json()["total"] == 6  # case-insensitive collection match
    assert client.get(prefix + "/jobs", params={"q": "100%_"}).json()["total"] == 0  # LIKE wildcards are literal
