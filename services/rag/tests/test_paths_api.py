"""Collection paths: ensure by name, add/re-point/remove paths, guarded removal and the full-path file API."""
import os
import time
from datetime import datetime
from uuid import uuid4

import pytest
from test_workspace import collection, update

# env and workspace_postgres come from conftest.py


def ensure(env, name, **body):
    return env[1].put(env[3] + f"/collections/by-name/{name}", json=body)


def add(env, name, source, **owner):
    return env[1].put(env[3] + f"/collections/by-name/{name}/paths", json={"source_path": source, **owner})


def listing(env):
    return {row["name"]: row for row in env[1].get(env[3] + "/collections").json()["collections"]}


def status(env, collection_id):
    return env[1].get(env[3] + f"/collections/{collection_id}").json()


def sources(row):
    return [(p["source_path"], p["owner_kind"], p["owner_key"]) for p in row["paths"]]


def test_ensure_creates_once_and_never_changes_settings(env):
    created = ensure(env, "payments", description="Payments knowledge")
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["name"] == "payments" and row["description"] == "Payments knowledge" and row["paths"] == []
    assert row["include"] == ["**/*.md", "**/*.markdown", "**/*.txt"] and row["search_mode"] == "full_text"
    assert update(env, row["id"], chunk_size=1000).status_code == 200
    again = ensure(env, "payments", description="Other text").json()
    assert again["id"] == row["id"] and again["description"] == "Payments knowledge" and again["chunk_size"] == 1000
    for bad in ("Payments", "a--b", "x" * 61):
        assert ensure(env, bad).status_code == 400, bad


def test_add_path_is_idempotent_and_bumps_revision_once(env):
    roots = env[6]
    (roots["code"] / "payments").mkdir()
    first = add(env, "payments", "code/payments")
    assert first.status_code == 200, first.text
    row = first.json()
    assert sources(row) == [("code/payments", None, None)]
    assert row["paths"][0]["host_path"] == "/srv/code/payments" and row["paths"][0]["present"] is True
    assert row["paths"][0]["files"] == 0
    again = add(env, "payments", "code/payments").json()
    assert again["revision"] == row["revision"] and len(again["paths"]) == 1
    added = add(env, "payments", "rag-data/jira", owner_kind="jira").json()
    assert added["revision"] == row["revision"] + 1
    assert sources(added) == [("code/payments", None, None), ("rag-data/jira", "jira", None)]
    assert (roots["rag-data"] / "jira").is_dir()  # rag-data paths are created for the source that writes there
    assert add(env, "payments", "docs/absent").status_code == 200  # missing folders are allowed and reported
    assert not (roots["docs"] / "absent").exists()
    for bad in ({"source_path": "../etc"}, {"source_path": "tmp/x"}, {"source_path": "code/x", "owner_key": "x"},
                {"source_path": "code/x", "owner_kind": "wiki"}):
        assert env[1].put(env[3] + "/collections/by-name/payments/paths", json=bad).status_code == 400, bad


def test_overlapping_path_is_refused_and_nothing_changes(env):
    roots = env[6]
    (roots["code"] / "payments" / "src").mkdir(parents=True)
    row = add(env, "payments", "code/payments").json()
    for source in ("code/payments/src", "code"):
        refused = add(env, "payments", source)
        assert refused.status_code == 409, source
        assert refused.json()["error"]["message"] == "Path overlaps code/payments in collection payments"
    owned = add(env, "payments", "code", owner_kind="jira")
    assert owned.status_code == 409 and "overlaps code/payments" in owned.json()["error"]["message"]
    after = listing(env)["payments"]
    assert sources(after) == [("code/payments", None, None)] and after["revision"] == row["revision"]
    assert add(env, "other", "code/payments/src").status_code == 200  # another collection may share it


def test_owner_path_moves_between_collections(env):
    owner = {"owner_kind": "code_project", "owner_id": str(uuid4()), "owner_key": "a"}
    first = add(env, "one", "code/a", **owner).json()
    repointed = add(env, "one", "code/b", **owner).json()
    assert sources(repointed) == [("code/b", "code_project", "a")] and repointed["revision"] == first["revision"] + 1
    moved = add(env, "two", "code/b", **owner).json()
    assert sources(moved) == [("code/b", "code_project", "a")]
    rows = listing(env)
    assert rows["one"]["paths"] == [] and rows["one"]["revision"] == repointed["revision"] + 1
    adopted = add(env, "three", "docs/runbooks").json()
    claimed = add(env, "three", "docs/runbooks", owner_kind="jira").json()
    assert sources(claimed) == [("docs/runbooks", "jira", None)] and claimed["revision"] == adopted["revision"]


def test_remove_by_source_path_or_owner_is_idempotent(env):
    client, prefix = env[1], env[3]
    add(env, "ops", "docs/runbooks")
    add(env, "ops", "rag-data/jira", owner_kind="jira")
    remove = lambda **params: client.delete(prefix + "/collections/by-name/ops/paths", params=params)
    owned = remove(source_path="rag-data/jira")
    assert owned.status_code == 409
    assert owned.json()["error"]["message"] == "Path rag-data/jira belongs to Jira; switch it off there"
    before = listing(env)["ops"]["revision"]
    assert remove(source_path="docs/runbooks").json() == {"removed": 1}
    assert listing(env)["ops"]["revision"] == before + 1
    assert remove(source_path="docs/runbooks").json() == {"removed": 0}
    assert remove(owner_kind="jira").json() == {"removed": 1}
    assert client.delete(prefix + "/collections/by-name/nope/paths", params={"owner_kind": "jira"}).json() == {"removed": 0}
    assert remove().status_code == 400
    assert remove(source_path="docs/x", owner_kind="jira").status_code == 400
    assert listing(env)["ops"]["paths"] == []


def test_owner_removal_clears_every_collection(env):
    client, prefix = env[1], env[3]
    project_id = str(uuid4())
    add(env, "one", "code/a", owner_kind="code_project", owner_id=project_id, owner_key="a")
    add(env, "two", "code/b", owner_kind="code_project", owner_id=project_id, owner_key="b")
    add(env, "two", "docs/keep")
    one_key = client.delete(prefix + "/paths", params={"owner_kind": "code_project", "owner_id": project_id, "owner_key": "a"})
    assert one_key.json() == {"removed": 1}
    assert client.delete(prefix + "/paths", params={"owner_kind": "code_project", "owner_id": project_id}).json() == {"removed": 1}
    rows = listing(env)
    assert rows["one"]["paths"] == [] and sources(rows["two"]) == [("docs/keep", None, None)]
    assert client.delete(prefix + "/paths", params={"owner_kind": "code_project", "owner_id": project_id}).json() == {"removed": 0}
    assert client.delete(prefix + "/paths").status_code == 400


def test_settings_only_edit_and_remove_guarded_by_owners(env):
    client, prefix = env[1], env[3]
    row = add(env, "jira", "rag-data/jira", owner_kind="jira").json()
    for extra in ({"name": "other"}, {"source_path": "rag-data/x"}):
        assert client.put(prefix + f"/collections/{row['id']}", json={**extra, "revision": row["revision"]}).status_code == 400
    saved = update(env, row["id"], description="Tickets", include=["**/*.md"])
    assert saved.status_code == 200 and saved.json()["name"] == "jira" and saved.json()["include"] == ["**/*.md"]
    assert saved.json()["status"] == "needs_refresh"
    refused = client.delete(prefix + f"/collections/{row['id']}", params={"revision": saved.json()["revision"], "confirmed": True})
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == "Remove this collection's sources first: Jira"
    client.delete(prefix + "/collections/by-name/jira/paths", params={"owner_kind": "jira"})
    revision = listing(env)["jira"]["revision"]
    assert client.delete(prefix + f"/collections/{row['id']}", params={"revision": revision, "confirmed": True}).status_code == 200
    assert "jira" not in listing(env)


def test_removed_endpoints(env):
    client, prefix = env[1], env[3]
    assert client.post(prefix + "/collections", json={"name": "x", "source_path": "rag-data/x"}).status_code == 405
    assert client.delete(prefix + "/file", params={"collection": "x", "path": "rag-data/x/a.md", "expected_hash": "h"}).status_code == 405


def test_status_reports_each_path_and_missing_paths(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    (roots["rag-data"] / "docs" / "a.md").write_text("# A")
    created = collection(env, "docs", "rag-data/docs", "docs/absent")
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
    add(env, "mixed", "docs/c")
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
        response = put(path)
        assert response.status_code == 400, path
    assert not (roots["code"] / "payments").exists()
    legacy = client.put(prefix + "/file", json={"collection": "jira", "path": "rag-data/jira/a.md", "content": "x"})
    assert legacy.status_code == 400


CODE_INCLUDE = ["**/*.md", "**/*.py", "**/*.ts"]


def test_creation_settings_apply_only_when_the_call_creates(env):
    created = ensure(env, "docs", description="Docs", include=["**/*.md"], exclude=["*/v[0-9]*/**"]).json()
    assert (created["description"], created["include"], created["exclude"]) == ("Docs", ["**/*.md"], ["*/v[0-9]*/**"])
    again = ensure(env, "docs", description="Other", include=["**/*.txt"], exclude=[]).json()
    assert (again["description"], again["include"], again["exclude"]) == ("Docs", ["**/*.md"], ["*/v[0-9]*/**"])
    owner = {"owner_kind": "code_project", "owner_id": str(uuid4()), "owner_key": "api"}
    added = add(env, "payments-api", "code/api", include=CODE_INCLUDE, **owner).json()
    assert added["include"] == CODE_INCLUDE and added["exclude"] == []
    update(env, added["id"], include=["**/*.md"])
    kept = add(env, "payments-api", "code/shared", include=CODE_INCLUDE,
               **{**owner, "owner_key": "shared"}).json()
    assert kept["include"] == ["**/*.md"]  # an existing collection is never changed
    assert add(env, "plain", "docs/plain").json()["include"] == ["**/*.md", "**/*.markdown", "**/*.txt"]
    for bad in ({"include": []}, {"include": ["/abs"]}, {"exclude": ["x"] * 51}, {"chunk_size": 500}):
        assert ensure(env, "bad", **bad).status_code == 400, bad
        assert add(env, "bad", "docs/bad", **bad).status_code == 400, bad
    assert "bad" not in listing(env)


def test_documents_source_ensures_itself_from_an_empty_workspace(env):
    """What admin-api does at startup, on a database with migrations applied and no RAG workspace."""
    with env[0].connect() as conn:
        conn.execute("DELETE FROM ka.rag_workspaces")
    exclude = ["*/v[0-9]*/**", "services/*/*/v[0-9]*/**"]
    for _ in range(2):  # idempotent
        assert ensure(env, "documents", exclude=exclude).status_code == 200
        row = add(env, "documents", "generated-documents", owner_kind="documents")
        assert row.status_code == 200, row.text
    row = listing(env)["documents"]
    assert row["exclude"] == exclude and sources(row) == [("generated-documents", "documents", None)]
    assert row["revision"] == 2  # created, then the path added once


def test_status_presence_is_per_path_even_when_the_walk_stops(env, monkeypatch):
    import rag.collections
    roots = env[6]
    (roots["code"] / "big").mkdir()
    for index in range(5):
        (roots["code"] / "big" / f"{index}.md").write_text("# x")
    created = collection(env, "big", "code/big", "docs/absent")
    monkeypatch.setattr(rag.collections, "MAX_STATUS_ENTRIES", 2)
    row = status(env, created)
    assert row["truncated"] is True
    assert row["status"] == "folder_missing" and row["missing_paths"] == ["docs/absent"]
    assert {p["source_path"]: p["present"] for p in row["paths"]} == {"code/big": True, "docs/absent": False}
    queued = env[1].post(env[3] + f"/collections/{created}/sync", json={})
    assert queued.status_code == 409 and "Path docs/absent is missing" in queued.json()["error"]["message"]


def wait_for_lock(env, text):
    """Block until a backend waits on a lock in a statement containing `text`."""
    for _ in range(200):
        with env[0].connect() as conn:
            if conn.execute("""SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE %s""",
                            (f"%{text}%",)).fetchone():
                return
        time.sleep(.05)
    raise AssertionError(f"Nothing waited on {text}")


def in_thread(call):
    import threading
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("response", call()))
    thread.start()
    return thread, result


def test_concurrent_first_add_of_one_owner_retries_instead_of_failing(env):
    ensure(env, "one")
    ensure(env, "two")
    with env[0].connect() as other:  # another request's first add of the same owner, not yet committed
        one = other.execute("SELECT id FROM ka.rag_collections WHERE name='one'").fetchone()["id"]
        other.execute("INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind) VALUES (%s,'rag-data/jira','jira')", (one,))
        thread, result = in_thread(lambda: add(env, "two", "rag-data/jira", owner_kind="jira"))
        wait_for_lock(env, "INSERT INTO ka.rag_collection_paths")
    thread.join(10)
    response = result["response"]
    assert response.status_code == 200, response.text  # the retry moved the committed path
    rows = listing(env)
    assert rows["one"]["paths"] == [] and sources(rows["two"]) == [("rag-data/jira", "jira", None)]


def test_add_path_answers_409_when_the_retry_also_races(env, monkeypatch):
    import psycopg
    import rag.workspace_api
    calls = []

    def racing(conn, ids):
        calls.append(ids)
        raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")
    monkeypatch.setattr(rag.workspace_api, "bump", racing)
    response = add(env, "one", "rag-data/jira", owner_kind="jira")
    assert response.status_code == 409 and len(calls) == 2
    assert response.json()["error"]["message"] == "The source path changed concurrently; retry"
    assert "one" not in listing(env)  # both attempts rolled back


@pytest.mark.parametrize("change", ["move", "remove"])
def test_path_changes_wait_for_a_publishing_sync_instead_of_deadlocking(env, change):
    """A sync locks its collection, then the workspace, then touches path rows; owner changes lock collections first."""
    owner = {"owner_kind": "code_project", "owner_id": str(uuid4()), "owner_key": "api"}
    add(env, "one", "code/api", **owner)
    ensure(env, "two")
    client, prefix, project = env[1], env[3], env[2]
    with env[0].connect() as sync:
        sync.execute("SELECT * FROM ka.rag_collections WHERE name='one' FOR UPDATE")
        if change == "move":
            thread, result = in_thread(lambda: add(env, "two", "code/api", **owner))
        else:
            thread, result = in_thread(lambda: client.delete(prefix + "/paths", params={k: v for k, v in owner.items() if k != "owner_key"}))
        wait_for_lock(env, "ka.rag_collection")
        sync.execute("SELECT * FROM ka.rag_workspaces WHERE project_id=%s FOR UPDATE", (project,))
        sync.execute("SELECT * FROM ka.rag_collection_paths WHERE source_path='code/api' FOR KEY SHARE")
    thread.join(10)
    assert result["response"].status_code == 200, result["response"].text
    rows = listing(env)
    assert rows["one"]["paths"] == []
    assert sources(rows["two"]) == ([("code/api", "code_project", "api")] if change == "move" else [])
