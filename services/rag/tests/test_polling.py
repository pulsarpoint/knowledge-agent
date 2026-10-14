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
        return conn.execute(
            'SELECT * FROM ka.jobs WHERE rag_collection_id=%s ORDER BY created_at', (collection_id,)
        ).fetchall()


def synced(env, name='docs'):
    """A collection with auto-sync on, synced once, whose folder and files are older than the quiet period."""
    folder = env[6]['jira'] / name
    folder.mkdir()
    (folder / 'a.md').write_text('# A\n\nalpha')
    for path in (folder / 'a.md', folder):
        age(path, 600)  # before the sync, so the log holds these mtimes
    created = collection(env, name, f'jira/{name}')
    sync(env, created)
    saved = update(env, created, auto_sync=True, poll_interval_minutes=5)
    assert saved.status_code == 200, saved.text
    return created, folder


def change(folder, name='b.md', seconds=300):
    (folder / name).write_text(f'# {name}\n\nnew')
    age(folder / name, seconds)
    age(folder, seconds)


def test_a_due_collection_with_a_quiet_change_is_queued_and_runs(env):
    created, folder = synced(env)
    change(folder)
    assert poll(env[0]) == {'docs': 'queued'}
    queued = jobs(env, created)[-1]
    assert (queued['status'], queued['input'], queued['requested_by']) == ('queued', {'auto': True}, None)
    with env[0].connect() as conn:
        assert conn.execute('SELECT last_polled_at FROM ka.rag_collections WHERE id=%s', (created,)).fetchone()[
            'last_polled_at'
        ]
    job = claim(env[0])
    assert process(env[0], job, env[5])['parsed_files'] == 1
    listed = env[1].get(env[3] + '/jobs').json()['jobs'][0]
    assert listed['input'] == {'auto': True} and listed['status'] == 'succeeded'


def test_an_unchanged_collection_is_not_queued(env):
    created, _ = synced(env)
    assert poll(env[0]) == {'docs': 'fresh'} and len(jobs(env, created)) == 1


def test_files_copied_with_old_mtimes_are_noticed(env):
    created, folder = synced(env)
    change(folder, 'copied.md', seconds=86400)  # rsync -a keeps the source's day-old mtime, also on the folder
    assert poll(env[0]) == {'docs': 'queued'}


def test_a_change_less_than_two_minutes_old_waits_for_the_quiet_period(env):
    created, folder = synced(env)
    (folder / 'b.md').write_text('# B\n\nstill being written')
    assert QUIET_SECONDS == 120
    assert poll(env[0]) == {'docs': 'changing'} and len(jobs(env, created)) == 1
    with env[0].connect() as conn:  # the next interval
        conn.execute('UPDATE ka.rag_collections SET last_polled_at=NULL WHERE id=%s', (created,))
    assert poll(env[0], now=time.time() + QUIET_SECONDS + 1) == {'docs': 'queued'}


def test_a_collection_that_is_not_due_is_not_checked(env, monkeypatch):
    created, folder = synced(env)
    change(folder)
    with env[0].connect() as conn:
        conn.execute(
            "UPDATE ka.rag_collections SET last_polled_at=now() - interval '4 minutes' WHERE id=%s", (created,)
        )
    monkeypatch.setattr(
        env[0].data_root, 'walk_paths', lambda *a, **k: pytest.fail('A collection that is not due must not be walked')
    )
    assert poll(env[0]) == {}
    monkeypatch.undo()
    with env[0].connect() as conn:
        conn.execute(
            "UPDATE ka.rag_collections SET last_polled_at=now() - interval '6 minutes' WHERE id=%s", (created,)
        )
    assert poll(env[0]) == {'docs': 'queued'}


def test_an_active_sync_prevents_a_second_one(env):
    created, folder = synced(env)
    change(folder)
    assert env[1].post(env[3] + f'/collections/{created}/sync', json={}).status_code == 202
    assert poll(env[0]) == {'docs': 'active'} and len(jobs(env, created)) == 2


def test_two_pollers_queue_one_sync(env):
    created, folder = synced(env)
    change(folder)
    results = []
    threads = [threading.Thread(target=lambda: results.append(poll(env[0]))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(results, key=len) == [{}, {'docs': 'queued'}]
    assert [job['status'] for job in jobs(env, created)] == ['succeeded', 'queued']


def test_auto_sync_off_is_never_polled(env, monkeypatch):
    (env[6]['jira'] / 'docs').mkdir()
    created = collection(env)
    monkeypatch.setattr(
        env[0].data_root, 'walk_paths', lambda *a, **k: pytest.fail('Manual collections are never walked')
    )
    assert poll(env[0]) == {}
    with env[0].connect() as conn:
        assert (
            conn.execute('SELECT last_polled_at FROM ka.rag_collections WHERE id=%s', (created,)).fetchone()[
                'last_polled_at'
            ]
            is None
        )


def test_a_missing_folder_is_not_synced(env):
    created, folder = synced(env)
    (folder / 'a.md').unlink()
    folder.rmdir()
    assert poll(env[0]) == {'docs': 'missing'} and len(jobs(env, created)) == 1
    assert env[1].get(env[3] + f'/collections/{created}').json()['status'] == 'folder_missing'


def test_poll_interval_is_validated_and_poll_settings_do_not_mark_the_collection_stale(env):
    created, _ = synced(env)
    for minutes in (0, 1441):
        assert update(env, created, poll_interval_minutes=minutes).status_code == 400, minutes
    row = env[1].get(env[3] + f'/collections/{created}').json()
    assert (row['auto_sync'], row['poll_interval_minutes'], row['status']) == (True, 5, 'up_to_date')
    fast = env[1].post(env[3] + '/collections', json={'name': 'fast', 'auto_sync': True, 'poll_interval_minutes': 1})
    assert fast.status_code == 201 and (fast.json()['auto_sync'], fast.json()['poll_interval_minutes']) == (True, 1)
    assert env[1].post(env[3] + '/collections', json={'name': 'slow', 'poll_interval_minutes': 1441}).status_code == 400


def test_the_worker_polls_when_no_sync_is_queued(env):
    created, folder = synced(env)
    change(folder)
    assert step(env[0], env[5]) is False  # nothing queued: the poller queues the change
    assert step(env[0], env[5]) is True  # the automatic sync runs
    row = env[1].get(env[3] + f'/collections/{created}').json()
    assert row['indexed_files'] == 2 and row['job']['status'] == 'succeeded'


def status(env, collection_id):
    return env[1].get(env[3] + f'/collections/{collection_id}').json()['status']


def detail(env, collection_id):
    return env[1].get(env[3] + f'/collections/{collection_id}').json()


def test_an_over_limit_collection_is_reported_and_never_queued(env, monkeypatch):
    import rag.collections

    created, folder = synced(env)
    change(folder)
    monkeypatch.setattr(rag.collections, 'MAX_FILES', 1)
    assert poll(env[0]) == {'docs': 'too_large'} and len(jobs(env, created)) == 1
    row = detail(env, created)
    assert row['too_large'] is True and row['status'] == 'needs_refresh'


def test_too_many_entries_is_too_large_for_status_and_poller(env, monkeypatch):
    import rag.collections

    created, folder = synced(env)
    for index in range(5):
        (folder / f'{index}.py').write_text('x')
    age(folder, 600)
    monkeypatch.setattr(rag.collections, 'MAX_SYNC_ENTRIES', 3)
    assert detail(env, created)['too_large'] is True
    assert poll(env[0]) == {'docs': 'too_large'} and len(jobs(env, created)) == 1


def test_a_large_code_folder_that_syncs_is_not_too_large(env):
    """More than 20,000 entries but one indexable file: sync, status and poller agree it fits."""
    folder = env[6]['code'] / 'big'
    folder.mkdir()
    for index in range(25_000):
        (folder / f'm{index}.py').touch()
    (folder / 'readme.md').write_text('# Readme\n\nhello')
    age(folder / 'readme.md', 600)
    created = collection(env, 'big', 'code/big')
    stats, row = sync(env, created)
    assert stats is not None and row['indexed_files'] == 1
    assert (row['status'], row['too_large'], row['truncated'], row['file_count']) == ('up_to_date', False, False, 1)
    assert update(env, created, auto_sync=True).status_code == 200
    assert poll(env[0]) == {'big': 'fresh'}
    (folder / 'new.md').write_text('# New\n\nfresh')
    age(folder / 'new.md', 600)
    age(folder, 600)
    row = detail(env, created)
    assert (row['status'], row['too_large']) == ('needs_refresh', False)
    with env[0].connect() as conn:
        conn.execute('UPDATE ka.rag_collections SET last_polled_at=NULL WHERE id=%s', (created,))
    assert poll(env[0]) == {'big': 'queued'}


def test_over_200_mb_of_text_is_too_large_for_status_poller_and_refresh_all(env):
    folder = env[6]['jira'] / 'huge'
    folder.mkdir()
    for index in range(107):
        with open(folder / f'{index}.md', 'wb') as handle:
            handle.truncate(2_000_000)  # sparse: 2 MB each by size, within the default file size limit
    created = collection(env, 'huge', auto_sync=True)
    row = detail(env, created)
    assert (row['status'], row['too_large'], row['truncated']) == ('needs_refresh', True, False)
    assert poll(env[0]) == {'huge': 'too_large'} and jobs(env, created) == []
    assert env[1].post(env[3] + '/collections/sync-stale').json() == {'queued': []}
    assert jobs(env, created) == []


def test_a_folder_vanishing_during_the_walk_is_missing(env, monkeypatch):
    created, folder = synced(env)
    for path in folder.iterdir():
        path.unlink()
    folder.rmdir()
    monkeypatch.setattr(env[0].data_root, 'present', lambda source: True)  # gone between the check and the walk
    assert poll(env[0]) == {'docs': 'missing'} and len(jobs(env, created)) == 1


def test_a_changing_collection_is_checked_again_after_the_quiet_period(env):
    created, folder = synced(env)
    (folder / 'b.md').write_text('# B\n\nstill being written')
    assert poll(env[0]) == {'docs': 'changing'}
    with env[0].connect() as conn:
        early = conn.execute(
            """SELECT extract(epoch FROM last_polled_at - (now() - make_interval(mins => poll_interval_minutes)))
            AS early FROM ka.rag_collections WHERE id=%s""",
            (created,),
        ).fetchone()['early']
    assert float(early) == pytest.approx(QUIET_SECONDS, abs=5)  # due QUIET_SECONDS from now, not a full interval


def test_one_failing_collection_does_not_stop_the_others(env, monkeypatch):
    from rag import workspace_poll

    _, folder = synced(env)
    _, other_folder = synced(env, 'other')
    change(folder)
    change(other_folder)
    real = workspace_poll.check

    def check(workspace, row, now):
        if row['name'] == 'docs':
            raise RuntimeError('boom')
        return real(workspace, row, now)

    monkeypatch.setattr(workspace_poll, 'check', check)
    assert poll(env[0]) == {'docs': 'error', 'other': 'queued'}


def test_status_and_poller_agree_on_excluded_and_included_changes(env):
    created, folder = synced(env)
    time.sleep(0.01)
    (folder / 'notes.bin').write_bytes(b'\x00ignored')  # not included; its and the folder's mtime are after the sync
    assert status(env, created) == 'up_to_date'
    assert poll(env[0]) == {'docs': 'fresh'}
    with env[0].connect() as conn:
        conn.execute('UPDATE ka.rag_collections SET last_polled_at=NULL WHERE id=%s', (created,))
    change(folder)
    assert status(env, created) == 'needs_refresh'
    assert poll(env[0]) == {'docs': 'queued'}


def test_a_collection_deleted_during_the_check_gets_no_job(env, monkeypatch):
    from rag import workspace_poll

    created, folder = synced(env)
    change(folder)
    real = workspace_poll.needs_sync

    def deleted_meanwhile(row, walk, log):
        with env[0].connect() as conn:
            conn.execute('UPDATE ka.rag_collections SET deleted_at=now() WHERE id=%s', (created,))
        return real(row, walk, log)

    monkeypatch.setattr(workspace_poll, 'needs_sync', deleted_meanwhile)
    assert poll(env[0]) == {'docs': 'deleted'} and len(jobs(env, created)) == 1
