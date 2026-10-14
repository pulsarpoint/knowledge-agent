"""The scheduler claims due schedules with a lease, runs their target and records the result."""

import threading
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from knowledge_admin.events import scheduler
from knowledge_admin.events.schedules import MISSING, TargetMissing


def a_schedule(store, **values) -> dict:
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        row = {
            'project_id': project,
            'name': 'Jira auto pull',
            'target_kind': 'jira.pull',
            'target_id': uuid4(),
            'cron': '*/15 * * * *',
            'timezone': 'UTC',
            **values,
        }
        schedule = conn.execute(
            f"""INSERT INTO ka.schedules({','.join(row)}) VALUES ({','.join(['%s'] * len(row))})
            RETURNING id""",
            tuple(row.values()),
        ).fetchone()
        conn.execute(
            "UPDATE ka.schedules SET next_run_at=coalesce(next_run_at, now()-interval '1 minute') WHERE id=%s AND enabled",
            (schedule['id'],),
        )
    return schedule


def row(store, schedule) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.schedules WHERE id=%s', (schedule['id'],)).fetchone()


def runs(store, schedule) -> list[dict]:
    with store.connect() as conn:
        return conn.execute(
            'SELECT * FROM ka.schedule_runs WHERE schedule_id=%s ORDER BY id', (schedule['id'],)
        ).fetchall()


def ok(store, schedule):
    return 'succeeded', 'Pulled 3 of 3 tickets.'


def test_a_due_schedule_runs_once_and_moves_to_its_next_time(store):
    schedule = a_schedule(store)
    assert scheduler.run_once(store, {'jira.pull': ok}) is True
    assert scheduler.run_once(store, {'jira.pull': ok}) is False
    saved = row(store, schedule)
    assert saved['last_status'] == 'succeeded' and saved['last_message'] == 'Pulled 3 of 3 tickets.'
    assert saved['lease_expires_at'] is None and saved['run_requested'] is False
    assert saved['next_run_at'] > datetime.now(UTC) and saved['next_run_at'].minute % 15 == 0
    assert [(r['trigger'], r['status']) for r in runs(store, schedule)] == [('schedule', 'succeeded')]


def test_missed_slots_run_once(store):
    schedule = a_schedule(store)
    with store.connect() as conn:
        conn.execute("UPDATE ka.schedules SET next_run_at=now()-interval '3 hours' WHERE id=%s", (schedule['id'],))
    assert scheduler.run_once(store, {'jira.pull': ok}) is True
    assert scheduler.run_once(store, {'jira.pull': ok}) is False
    assert len(runs(store, schedule)) == 1


def test_a_claimed_schedule_is_not_claimed_again(store):
    a_schedule(store)
    assert scheduler.claim(store) is not None
    assert scheduler.claim(store) is None


def test_expired_lease_is_claimable_and_the_stale_finish_records_nothing(store):
    schedule = a_schedule(store)
    stale = scheduler.claim(store)
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.schedules SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (schedule['id'],)
        )
    fresh = scheduler.claim(store)
    assert fresh is not None
    scheduler.finish(store, stale, 'failed', 'late')
    assert runs(store, schedule) == [] and row(store, schedule)['lease_expires_at'] is not None
    scheduler.finish(store, fresh, 'succeeded', 'done')
    assert [r['message'] for r in runs(store, schedule)] == ['done']


def test_failures_are_recorded_and_the_schedule_moves_on(store):
    def jira_down(store, schedule):
        raise HTTPException(502, 'Could not reach Jira. Check the URL, network access, and TLS certificate')

    def broken(store, schedule):
        raise RuntimeError('secret-looking detail')

    def gone(store, schedule):
        raise TargetMissing()

    for target, message in [
        (jira_down, 'Could not reach Jira. Check the URL, network access, and TLS certificate'),
        (broken, scheduler.UNEXPECTED),
        (gone, MISSING),
    ]:
        schedule = a_schedule(store)
        assert scheduler.run_once(store, {'jira.pull': target}) is True
        saved = row(store, schedule)
        assert (saved['last_status'], saved['last_message']) == ('failed', message)
        assert saved['next_run_at'] > datetime.now(UTC) and saved['lease_expires_at'] is None


def test_an_unknown_target_fails(store):
    schedule = a_schedule(store)
    assert scheduler.run_once(store, {}) is True
    assert row(store, schedule)['last_message'] == MISSING


def test_a_skipped_run_is_recorded_as_skipped(store):
    schedule = a_schedule(store)
    scheduler.run_once(
        store, {'jira.pull': lambda store, schedule: ('skipped', 'A manual fetch ran at the same time.')}
    )
    assert row(store, schedule)['last_status'] == 'skipped'


def test_pausing_during_a_run_keeps_it_paused(store):
    schedule = a_schedule(store)

    def pause_meanwhile(store, claimed):
        with store.connect() as conn:
            conn.execute(
                "UPDATE ka.schedules SET enabled=false,next_run_at=NULL,cron='0 * * * *' WHERE id=%s", (claimed['id'],)
            )
        return ok(store, claimed)

    scheduler.run_once(store, {'jira.pull': pause_meanwhile})
    saved = row(store, schedule)
    assert saved['next_run_at'] is None and saved['cron'] == '0 * * * *' and saved['last_status'] == 'succeeded'


def test_run_now_on_a_paused_schedule_runs_once_and_stays_paused(store):
    schedule = a_schedule(store, enabled=False)
    with store.connect() as conn:
        conn.execute('UPDATE ka.schedules SET run_requested=true,next_run_at=now() WHERE id=%s', (schedule['id'],))
    assert scheduler.run_once(store, {'jira.pull': ok}) is True
    saved = row(store, schedule)
    assert saved['next_run_at'] is None and saved['run_requested'] is False and saved['enabled'] is False
    assert [r['trigger'] for r in runs(store, schedule)] == ['manual']


def test_history_keeps_the_newest_100(store):
    schedule = a_schedule(store)
    with store.connect() as conn:
        for _ in range(105):
            conn.execute(
                """INSERT INTO ka.schedule_runs(schedule_id,trigger,started_at,finished_at,status,message)
                VALUES (%s,'schedule',now(),now(),'succeeded','old')""",
                (schedule['id'],),
            )
    scheduler.run_once(store, {'jira.pull': ok})
    kept = runs(store, schedule)
    assert len(kept) == scheduler.KEEP_RUNS and kept[-1]['message'] == 'Pulled 3 of 3 tickets.'


def test_loop_returns_when_stopped(store):
    stopped = threading.Event()
    stopped.set()
    scheduler.loop(store, stopped, {'jira.pull': ok})  # returns at once


def test_loop_survives_an_unexpected_error(store, monkeypatch):
    stopped = threading.Event()
    calls = []

    def flaky(store, targets=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('boom')
        stopped.set()
        return False

    monkeypatch.setattr(scheduler, 'run_once', flaky)
    monkeypatch.setattr(scheduler, 'POLL_SECONDS', 0)
    scheduler.loop(store, stopped, {'jira.pull': ok})
    assert len(calls) == 2


def test_a_schedule_with_an_unusable_time_zone_is_recorded_as_failed_and_stops(store):
    schedule = a_schedule(store)

    def breaks_the_zone(store, schedule):
        with store.connect() as conn:
            conn.execute("UPDATE ka.schedules SET timezone='Mars/Base' WHERE id=%s", (schedule['id'],))
        return 'succeeded', 'Pulled 3 of 3 tickets.'

    assert scheduler.run_once(store, {'jira.pull': breaks_the_zone}) is True
    saved = row(store, schedule)
    assert saved['last_status'] == 'failed' and saved['last_message'] == scheduler.UNEXPECTED
    assert saved['next_run_at'] is None and saved['lease_expires_at'] is None and saved['run_requested'] is False
    assert [(r['status'], r['message']) for r in runs(store, schedule)] == [('failed', scheduler.UNEXPECTED)]


def test_main_stops_the_scheduler_when_the_repository_loop_crashes(tmp_path, monkeypatch):
    import base64
    import pytest
    from knowledge_admin.code_projects import worker as repository_worker

    ended = threading.Event()

    def waits_for_stop(store, stopped):
        stopped.wait()
        ended.set()

    def crash(store, code_root):
        raise RuntimeError('boom')

    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', base64.urlsafe_b64encode(b'k' * 32).decode())
    monkeypatch.setenv('AUTH_ISSUER_URL', 'http://issuer')
    monkeypatch.setenv('CODE_ROOT', str(tmp_path))
    monkeypatch.setattr(repository_worker, 'run_once', crash)
    monkeypatch.setattr(scheduler, 'loop', waits_for_stop)
    with pytest.raises(RuntimeError, match='boom'):
        repository_worker.main()
    assert ended.is_set()
