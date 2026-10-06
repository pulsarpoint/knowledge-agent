"""Collections managed in RAG: create, connect and disconnect folders, the folder browser and the file API."""
import os
import threading
import time
from datetime import datetime
from uuid import uuid4

import pytest
from test_workspace import add_folder, collection, create, sync

# env and workspace_postgres come from conftest.py


def listing(env):
    return {row["name"]: row for row in env[1].get(env[3] + "/collections").json()["collections"]}


def status(env, collection_id):
    return env[1].get(env[3] + f"/collections/{collection_id}").json()


def folders(row):
    return [p["source_path"] for p in row["paths"]]


def test_create_collection_with_settings_and_refuse_duplicates(env):
    client, prefix = env[1], env[3]
    created = client.post(prefix + "/collections", json={"name": "payments", "description": "Payments knowledge",
                                                          "include": ["**/*.py"]})
    assert created.status_code == 201, created.text
    row = created.json()
    assert (row["name"], row["description"], row["include"], row["paths"]) == ("payments", "Payments knowledge", ["**/*.py"], [])
    assert (row["search_mode"], row["embeddings_enabled"], row["exclude"], row["chunk_size"], row["chunk_overlap"]) == (
        "full_text", False, [], 2000, 200)
    duplicate = client.post(prefix + "/collections", json={"name": "payments"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["message"] == "Collection payments already exists"
    for bad in ({"name": "Payments"}, {"name": "a--b"}, {"name": "x" * 61}, {"name": "ok", "chunk_size": 100},
                {"name": "ok", "include": []}, {"name": "ok", "source_path": "rag-data/ok"}, {}):
        assert client.post(prefix + "/collections", json=bad).status_code == 400, bad
    assert set(listing(env)) == {"payments"}


def test_add_folder_creates_rag_data_folders_and_bumps_revision(env):
    roots = env[6]
    (roots["code"] / "payments").mkdir()
    row = create(env, "payments")
    added = add_folder(env, row["id"], "code/payments")
    assert added.status_code == 200, added.text
    assert folders(added.json()) == ["code/payments"] and added.json()["revision"] == row["revision"] + 1
    path = added.json()["paths"][0]
    assert path["host_path"] == "/srv/code/payments" and path["present"] is True and path["files"] == 0
    tickets = add_folder(env, row["id"], "rag-data/jira/tickets").json()
    assert folders(tickets) == ["code/payments", "rag-data/jira/tickets"]
    assert (roots["rag-data"] / "jira" / "tickets").is_dir()  # a missing rag-data folder is created
    assert add_folder(env, uuid4(), "code/payments").status_code == 404


def test_add_folder_rejects_overlap_missing_and_bad_paths_and_changes_nothing(env):
    roots = env[6]
    (roots["code"] / "payments" / "src").mkdir(parents=True)
    os.symlink(roots["code"] / "payments", roots["code"] / "linked")
    row = add_folder(env, create(env, "payments")["id"], "code/payments").json()
    for source in ("code/payments", "code/payments/src", "code"):
        refused = add_folder(env, row["id"], source)
        assert refused.status_code == 409, source
        assert refused.json()["error"]["message"] == "Folder overlaps code/payments in collection payments"
    for source in ("docs/absent", "code/linked"):
        missing = add_folder(env, row["id"], source)
        assert missing.status_code == 409 and missing.json()["error"]["message"] == "Folder does not exist", source
    assert not (roots["docs"] / "absent").exists()
    for source in ("../etc", "tmp/x", "code/payments/", "/code/payments", "code/a b", "code/.git", "code/../docs", ""):
        assert add_folder(env, row["id"], source).status_code == 400, source
    owned = env[1].post(env[3] + f"/collections/{row['id']}/paths", json={"source_path": "docs", "owner_kind": "jira"})
    assert owned.status_code == 400
    after = status(env, row["id"])
    assert folders(after) == ["code/payments"] and after["revision"] == row["revision"]
    other = create(env, "other")
    assert folders(add_folder(env, other["id"], "code/payments/src").json()) == ["code/payments/src"]  # collections may share


def test_remove_folder_only_removes_a_folder_of_that_collection(env):
    roots = env[6]
    (roots["docs"] / "runbooks").mkdir()
    one = collection(env, "one", "docs/runbooks")
    two = collection(env, "two", "docs/runbooks")
    row = status(env, one)
    path_id = row["paths"][0]["id"]
    wrong = env[1].delete(env[3] + f"/collections/{two}/paths/{path_id}")
    assert wrong.status_code == 404 and wrong.json()["error"]["message"] == "Unknown folder"
    assert folders(status(env, one)) == ["docs/runbooks"]
    removed = env[1].delete(env[3] + f"/collections/{one}/paths/{path_id}")
    assert removed.status_code == 200 and removed.json()["paths"] == [] and removed.json()["revision"] == row["revision"] + 1
    assert env[1].delete(env[3] + f"/collections/{one}/paths/{path_id}").status_code == 404
    assert (roots["docs"] / "runbooks").is_dir()


def test_remove_collection_is_always_allowed_and_keeps_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "jira").mkdir()
    (roots["rag-data"] / "jira" / "DEMO-1.md").write_text("# DEMO-1\n\nretries")
    created = collection(env, "jira", "rag-data/jira")
    _, row = sync(env, created)
    removed = client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"], "confirmed": True})
    assert removed.status_code == 200 and removed.json() == {"deleted_files": 1}
    assert "jira" not in listing(env) and (roots["rag-data"] / "jira" / "DEMO-1.md").is_file()
    with env[0].connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.rag_collection_paths WHERE collection_id=%s",
                            (created,)).fetchone()["n"] == 0


def test_folder_browser_lists_visible_subfolders_and_rejects_bad_paths(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("payments", "billing", ".git", ".hidden", "node_modules", "with space"):
        (roots["code"] / name).mkdir()
    (roots["code"] / "README.md").write_text("x")
    os.symlink(roots["code"] / "payments", roots["code"] / "link")
    top = client.get(prefix + "/folders").json()
    assert top["path"] is None and [f["path"] for f in top["folders"]] == [
        "code", "code-index", "rag-data", "generated-documents", "docs"]
    code = client.get(prefix + "/folders", params={"path": "code"}).json()
    assert code == {"path": "code", "truncated": False, "folders": [
        {"name": "billing", "path": "code/billing"}, {"name": "payments", "path": "code/payments"}]}
    for bad in ("../etc", "code/../docs", "/code", "code/.git", "tmp", "code/"):
        assert client.get(prefix + "/folders", params={"path": bad}).status_code == 400, bad
    missing = client.get(prefix + "/folders", params={"path": "code/absent"})
    assert missing.status_code == 404 and missing.json()["error"]["message"] == "Folder does not exist"


def test_ownership_endpoints_are_gone(env):
    client, prefix = env[1], env[3]
    assert client.put(prefix + "/collections/by-name/jira", json={}).status_code in (404, 405)
    assert client.put(prefix + "/collections/by-name/jira/paths", json={"source_path": "rag-data/jira"}).status_code in (404, 405)
    assert client.delete(prefix + "/collections/by-name/jira/paths", params={"owner_kind": "jira"}).status_code in (404, 405)
    assert client.delete(prefix + "/paths", params={"owner_kind": "jira"}).status_code in (404, 405)
    assert client.delete(prefix + "/file", params={"path": "rag-data/x/a.md"}).status_code == 405


def test_status_reports_each_path_and_missing_paths(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    (roots["rag-data"] / "docs" / "a.md").write_text("# A")
    (roots["docs"] / "absent").mkdir()
    created = collection(env, "docs", "rag-data/docs", "docs/absent")
    (roots["docs"] / "absent").rmdir()
    row = status(env, created)
    assert row["status"] == "folder_missing" and row["folder_present"] is False and row["missing_paths"] == ["docs/absent"]
    assert {p["source_path"]: p["present"] for p in row["paths"]} == {"docs/absent": False, "rag-data/docs": True}
    assert row["file_count"] == 1
    summary = env[1].get(env[3] + "/collections", params={"status": "false"}).json()["collections"][0]
    assert summary["missing_paths"] == ["docs/absent"] and "status" not in summary
    queued = env[1].post(env[3] + f"/collections/{created}/sync", json={})
    assert queued.status_code == 409 and "Path docs/absent is missing" in queued.json()["error"]["message"]


def test_folder_changed_is_newest_across_paths_and_path_changes_need_refresh(env):
    roots = env[6]
    old, new = time.time() - 7200, time.time() - 60
    for folder, stamp in ((roots["rag-data"] / "a", old), (roots["docs"] / "b", new)):
        folder.mkdir()
        (folder / "x.md").write_text("# X")
        for path in (folder / "x.md", folder):
            os.utime(path, (stamp, stamp))
    created = collection(env, "mixed", "rag-data/a", "docs/b")
    row = status(env, created)
    assert datetime.fromisoformat(row["folder_changed_at"]).timestamp() == pytest.approx(new, abs=1)
    assert row["file_count"] == 2
    with env[0].connect() as conn:  # as if a sync had just finished
        conn.execute("UPDATE ka.rag_collections SET last_synced_at=now(),synced_revision=revision WHERE id=%s", (created,))
    assert status(env, created)["status"] == "up_to_date"
    (roots["docs"] / "c").mkdir()
    add_folder(env, created, "docs/c")
    assert status(env, created)["status"] == "needs_refresh"


def test_file_api_serves_only_paths_under_the_collection(env):
    roots = env[6]
    repo = roots["code"] / "payments"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "retry.py").write_text("RETRIES = 5\n")
    (repo / ".env").write_text("SECRET=1")
    (roots["code"] / "other").mkdir()
    (roots["code"] / "other" / "x.py").write_text("print(2)\n")
    collection(env, "payments", "code/payments", include=["**/*.py", "**/.env"])
    client, prefix = env[1], env[3]
    get = lambda path: client.get(prefix + "/file", params={"collection": "payments", "path": path})
    found = get("code/payments/src/retry.py").json()
    assert found == {"collection": "payments", "path": "code/payments/src/retry.py", "content": "RETRIES = 5\n", "hash": found["hash"]}
    for path in ("src/retry.py", "code/other/x.py", "code/payments/.env", "code/paymentsX/src/retry.py", "code/payments"):
        response = get(path)
        assert response.status_code == 404, path
        assert "SECRET" not in response.text
    for path in ("../code/payments/src/retry.py", "/code/payments/src/retry.py", "code//payments/src/retry.py", ""):
        assert get(path).status_code == 400, path
    assert client.get(prefix + "/file", params={"collection": "nope", "path": "code/payments/src/retry.py"}).status_code == 404


def test_rag_data_files_are_read_by_full_path_without_a_collection(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "jira").mkdir()
    (roots["rag-data"] / "jira" / "DEMO-1.md").write_text("# DEMO-1")
    (roots["rag-data"] / "jira" / ".env").write_text("SECRET=1")
    (roots["rag-data"] / "top.md").write_text("top")
    (roots["code"] / "x.md").write_text("code")
    found = client.get(prefix + "/file", params={"path": "rag-data/jira/DEMO-1.md"})
    assert found.status_code == 200 and found.json()["content"] == "# DEMO-1" and found.json()["collection"] is None
    for path in ("rag-data/jira/.env", "code/x.md", "rag-data/jira/absent.md", "rag-data/top.md"):
        response = client.get(prefix + "/file", params={"path": path})
        assert response.status_code == 404, path
        assert "SECRET" not in response.text


def test_symlinked_files_are_never_served(env, tmp_path):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("outside-secret")
    (roots["rag-data"] / "docs" / "link.txt").symlink_to(secret)
    collection(env)
    response = env[1].get(env[3] + "/file", params={"collection": "docs", "path": "rag-data/docs/link.txt"})
    assert response.status_code == 400 and "outside-secret" not in response.text


def test_rag_data_writer_uses_full_paths(env):
    roots = env[6]
    client, prefix = env[1], env[3]
    put = lambda path, content="# A", expected=None: client.put(prefix + "/file", json={"path": path, "content": content, "expected_hash": expected})
    saved = put("rag-data/jira/DEMO-1.md", "# DEMO-1")
    assert saved.status_code == 200 and saved.json()["path"] == "rag-data/jira/DEMO-1.md"
    assert (roots["rag-data"] / "jira" / "DEMO-1.md").read_text() == "# DEMO-1"
    assert put("rag-data/jira/DEMO-1.md", "# stale").status_code == 409
    assert put("rag-data/jira/DEMO-1.md", "# DEMO-1 v2", saved.json()["hash"]).status_code == 200
    for path in ("code/payments/a.md", "rag-data", "rag-data/jira/.env", "rag-data/.git/config", "rag-data/node_modules/a.md", "../x.md"):
        assert put(path).status_code == 400, path
    assert not (roots["code"] / "payments").exists()


def test_status_presence_is_per_path_even_when_the_walk_stops(env, monkeypatch):
    import rag.collections
    roots = env[6]
    (roots["code"] / "big").mkdir()
    for index in range(5):
        (roots["code"] / "big" / f"{index}.md").write_text("# x")
    (roots["docs"] / "absent").mkdir()
    created = collection(env, "big", "code/big", "docs/absent")
    (roots["docs"] / "absent").rmdir()
    monkeypatch.setattr(rag.collections, "MAX_STATUS_ENTRIES", 2)
    row = status(env, created)
    assert row["truncated"] is True
    assert row["status"] == "folder_missing" and row["missing_paths"] == ["docs/absent"]
    assert {p["source_path"]: p["present"] for p in row["paths"]} == {"code/big": True, "docs/absent": False}


def wait_for_lock(env, text):
    """Block until a backend waits on a lock in a statement containing `text`."""
    for _ in range(200):
        with env[0].connect() as conn:
            if conn.execute("SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE %s",
                            (f"%{text}%",)).fetchone():
                return
        time.sleep(.05)
    raise AssertionError(f"Nothing waited on {text}")


def test_removing_a_folder_waits_for_a_publishing_sync_instead_of_deadlocking(env):
    """A publishing sync locks its collection, then the workspace; folder removal takes the same order."""
    roots = env[6]
    (roots["code"] / "api").mkdir()
    created = collection(env, "one", "code/api")
    path_id = status(env, created)["paths"][0]["id"]
    client, prefix, project = env[1], env[3], env[2]
    result = {}
    with env[0].connect() as publishing:
        publishing.execute("SELECT * FROM ka.rag_collections WHERE id=%s FOR UPDATE", (created,))
        thread = threading.Thread(target=lambda: result.setdefault(
            "response", client.delete(prefix + f"/collections/{created}/paths/{path_id}")))
        thread.start()
        wait_for_lock(env, "ka.rag_collections")
        publishing.execute("SELECT * FROM ka.rag_workspaces WHERE project_id=%s FOR UPDATE", (project,))
    thread.join(10)
    assert result["response"].status_code == 200, result["response"].text
    assert status(env, created)["paths"] == []
