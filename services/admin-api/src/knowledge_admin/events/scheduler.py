"""The scheduler: claim a due schedule, run its target, record the result. It runs as a thread in repository-worker;
the lease keeps several workers from running the same schedule, and a worker whose lease ran out records nothing."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from fastapi import HTTPException

from knowledge_admin.jira import sync as jira_sync
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.events.schedules import MISSING, TargetMissing, next_after

logger = logging.getLogger(__name__)

POLL_SECONDS = 5
KEEP_RUNS = 100
UNEXPECTED = 'The run failed unexpectedly. See the repository-worker log.'

Target = Callable[[RepositoryStore, dict], tuple[str, str]]
TARGETS: dict[str, Target] = {'jira.pull': jira_sync.auto_pull}

CLAIM = """UPDATE ka.schedules SET lease_expires_at=clock_timestamp()+interval '15 minutes', last_run_at=clock_timestamp()
    WHERE id=(SELECT id FROM ka.schedules WHERE next_run_at<=clock_timestamp()
        AND (lease_expires_at IS NULL OR lease_expires_at<clock_timestamp())
        ORDER BY next_run_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
    RETURNING *"""


def claim(store: RepositoryStore) -> dict | None:
    with store.connect() as conn:
        return conn.execute(CLAIM).fetchone()


def run(store: RepositoryStore, schedule: dict, targets: dict[str, Target]) -> tuple[str, str]:
    target = targets.get(schedule['target_kind'])
    if target is None:
        return 'failed', MISSING
    try:
        return target(store, schedule)
    except TargetMissing:
        return 'failed', MISSING
    except HTTPException as exc:
        return 'failed', str(exc.detail)
    except Exception:
        logger.exception('Schedule %s (%s) failed', schedule['id'], schedule['target_kind'])
        return 'failed', UNEXPECTED


def finish(store: RepositoryStore, schedule: dict, status: str, message: str) -> None:
    """Record the run and set the next time from the schedule as it is now (it may have been edited or paused)."""
    with store.connect() as conn:
        current = conn.execute(
            """SELECT cron,timezone,enabled FROM ka.schedules WHERE id=%s AND lease_expires_at=%s
            FOR UPDATE""",
            (schedule['id'], schedule['lease_expires_at']),
        ).fetchone()
        if current is None:
            return  # deleted, or the lease ran out and another worker took the schedule over
        next_run = None
        if current['enabled']:
            try:
                next_run = next_after(current['cron'], current['timezone'], datetime.now(UTC))
            except Exception:
                logger.exception(
                    'Schedule %s has no usable next run time; it will not run again until edited', schedule['id']
                )
                status, message = 'failed', UNEXPECTED
        conn.execute(
            """UPDATE ka.schedules SET lease_expires_at=NULL,run_requested=false,next_run_at=%s,
            last_finished_at=clock_timestamp(),last_status=%s,last_message=%s WHERE id=%s""",
            (next_run, status, message, schedule['id']),
        )
        conn.execute(
            """INSERT INTO ka.schedule_runs(schedule_id,trigger,started_at,finished_at,status,message)
            VALUES (%s,%s,%s,clock_timestamp(),%s,%s)""",
            (
                schedule['id'],
                'manual' if schedule['run_requested'] else 'schedule',
                schedule['last_run_at'],
                status,
                message,
            ),
        )
        conn.execute(
            """DELETE FROM ka.schedule_runs WHERE schedule_id=%s AND id NOT IN
            (SELECT id FROM ka.schedule_runs WHERE schedule_id=%s ORDER BY id DESC LIMIT %s)""",
            (schedule['id'], schedule['id'], KEEP_RUNS),
        )


def run_once(store: RepositoryStore, targets: dict[str, Target] | None = None) -> bool:
    schedule = claim(store)
    if schedule is None:
        return False
    status, message = run(store, schedule, TARGETS if targets is None else targets)
    finish(store, schedule, status, message)
    print(f'Schedule {schedule["id"]} ({schedule["target_kind"]}): {status}', flush=True)
    return True


def loop(store: RepositoryStore, stopped: threading.Event, targets: dict[str, Target] | None = None) -> None:
    while not stopped.is_set():
        try:
            worked = run_once(store, targets)
        except psycopg.Error:
            print('Scheduler database unavailable; retrying', flush=True)
            worked = False
        except Exception:
            logger.exception('Scheduler loop failed; retrying')
            worked = False
        if not worked:
            stopped.wait(POLL_SECONDS)
