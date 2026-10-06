"""Automatic sync: due collections are walked without reading files and queued once their folders are quiet."""
import os
import threading
import time

import pytest
from test_workspace import collection, sync, update

from rag.workspace_poll import QUIET_SECONDS, poll
from rag.workspace_worker import claim, process, step

# env and workspace_postgres come from conftest.py


def age(path, seconds):
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def jobs(env, collection_id):
    with env[0].connect() as conn:
        return conn.execute("SELECT * FROM ka.jobs WHERE rag_collection_id=%s ORDER BY created_at",
                            (collection_id,)).fetchall()


def synced(env, name="docs"):
    """A collection with auto-sync on, synced once, whose folder and files are older than the quiet period."""
    folder = env[6]["rag-data"] / name
    folder.mkdir()
    (folder / "a.md").write_text("# A\n\nalpha")
    for path in (folder / "a.md", folder):
        age(path, 600)  # before the sync, so the log holds these mtimes
    created = collection(env, name, f"rag-data/{name}")
    sync(env, created)
    saved = update(env, created, auto_sync=True, poll_interval_minutes=5)
    assert saved.status_code == 200, saved.text
    return created, folder


def change(folder, name="b.md", seconds=300):
    (folder / name).write_text(f"# {name}\n\nnew")
    age(folder / name, seconds)
    age(folder, seconds)


def test_a_due_collection_with_a_quiet_change_is_queued_and_runs(env):
    created, folder = synced(env)
    change(folder)
    assert poll(env[0]) == {"docs": "queued"}
    queued = jobs(env, created)[-1]
    assert (queued["status"], queued["input"], queued["requested_by"]) == ("queued", {"auto": True}, None)
    with env[0].connect() as conn:
        assert conn.execute("SELECT last_polled_at FROM ka.rag_collections WHERE id=%s", (created,)).fetchone()["last_polled_at"]
    job = claim(env[0])
    assert process(env[0], job, env[5])["parsed_files"] == 1
    listed = env[1].get(env[3] + "/jobs").json()["jobs"][0]
    assert listed["input"] == {"auto": True} and listed["status"] == "succeeded"


def test_an_unchanged_collection_is_not_queued(env):
    created, _ = synced(env)
    assert poll(env[0]) == {"docs": "fresh"} and len(jobs(env, created)) == 1


def test_files_copied_with_old_mtimes_are_noticed(env):
    created, folder = synced(env)
    change(folder, "copied.md", seconds=86400)  # rsync -a keeps the source's day-old mtime, also on the folder
    assert poll(env[0]) == {"docs": "queued"}


def test_a_change_less_than_two_minutes_old_waits_for_the_quiet_period(env):
    created, folder = synced(env)
    (folder / "b.md").write_text("# B\n\nstill being written")
    assert QUIET_SECONDS == 120
    assert poll(env[0]) == {"docs": "changing"} and len(jobs(env, created)) == 1
    with env[0].connect() as conn:  # the next interval
        conn.execute("UPDATE ka.rag_collections SET last_polled_at=NULL WHERE id=%s", (created,))
    assert poll(env[0], now=time.time() + QUIET_SECONDS + 1) == {"docs": "queued"}


def test_a_collection_that_is_not_due_is_not_checked(env, monkeypatch):
    created, folder = synced(env)
    change(folder)
    with env[0].connect() as conn:
        conn.execute("UPDATE ka.rag_collections SET last_polled_at=now() - interval '4 minutes' WHERE id=%s", (created,))
    monkeypatch.setattr(env[0].areas, "walk_paths", lambda *a, **k: pytest.fail("A collection that is not due must not be walked"))
    assert poll(env[0]) == {}
    monkeypatch.undo()
    with env[0].connect() as conn:
        conn.execute("UPDATE ka.rag_collections SET last_polled_at=now() - interval '6 minutes' WHERE id=%s", (created,))
    assert poll(env[0]) == {"docs": "queued"}


def test_an_active_sync_prevents_a_second_one(env):
    created, folder = synced(env)
    change(folder)
    assert env[1].post(env[3] + f"/collections/{created}/sync", json={}).status_code == 202
    assert poll(env[0]) == {"docs": "active"} and len(jobs(env, created)) == 2


def test_two_pollers_queue_one_sync(env):
    created, folder = synced(env)
    change(folder)
    results = []
    threads = [threading.Thread(target=lambda: results.append(poll(env[0]))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(results, key=len) == [{}, {"docs": "queued"}]
    assert [job["status"] for job in jobs(env, created)] == ["succeeded", "queued"]


def test_auto_sync_off_is_never_polled(env, monkeypatch):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    monkeypatch.setattr(env[0].areas, "walk_paths", lambda *a, **k: pytest.fail("Manual collections are never walked"))
    assert poll(env[0]) == {}
    with env[0].connect() as conn:
        assert conn.execute("SELECT last_polled_at FROM ka.rag_collections WHERE id=%s", (created,)).fetchone()["last_polled_at"] is None


def test_a_missing_folder_is_not_synced(env):
    created, folder = synced(env)
    (folder / "a.md").unlink()
    folder.rmdir()
    assert poll(env[0]) == {"docs": "missing"} and len(jobs(env, created)) == 1
    assert env[1].get(env[3] + f"/collections/{created}").json()["status"] == "folder_missing"


def test_poll_interval_is_validated_and_poll_settings_do_not_mark_the_collection_stale(env):
    created, _ = synced(env)
    for minutes in (0, 1441):
        assert update(env, created, poll_interval_minutes=minutes).status_code == 400, minutes
    row = env[1].get(env[3] + f"/collections/{created}").json()
    assert (row["auto_sync"], row["poll_interval_minutes"], row["status"]) == (True, 5, "up_to_date")
    fast = env[1].post(env[3] + "/collections", json={"name": "fast", "auto_sync": True, "poll_interval_minutes": 1})
    assert fast.status_code == 201 and (fast.json()["auto_sync"], fast.json()["poll_interval_minutes"]) == (True, 1)
    assert env[1].post(env[3] + "/collections", json={"name": "slow", "poll_interval_minutes": 1441}).status_code == 400


def test_the_worker_polls_when_no_sync_is_queued(env):
    created, folder = synced(env)
    change(folder)
    assert step(env[0], env[5]) is False  # nothing queued: the poller queues the change
    assert step(env[0], env[5]) is True  # the automatic sync runs
    row = env[1].get(env[3] + f"/collections/{created}").json()
    assert row["indexed_files"] == 2 and row["job"]["status"] == "succeeded"
