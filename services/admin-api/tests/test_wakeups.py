"""Wake-ups (0123): an agent queues a message to its own session for later (schedule_wakeup, list_wakeups,
cancel_wakeup); the worker delivers a due one as the session's next turn, never mid-turn; idle close keeps a session
with one pending; Close discards them, Stop keeps them."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from knowledge_admin.agents.runner import instructions_and_tools
from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities.wakeups import (
    cancel_pending,
    check_schedule_wakeup,
    due_time,
)
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.subagents import subagent_config
from knowledge_admin.sessions.text import close_idle
from knowledge_admin.tools.catalog import CATALOG
from session_rows import a_model, add_ad_hoc_session
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_tool_catalog import config as run_config

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0123_session_wakeups.up.sql', '0123_session_wakeups.down.sql'
WAKEUPS = ['schedule_wakeup', 'list_wakeups', 'cancel_wakeup']
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
TOO_MANY = 'A session can have at most 3 pending wake-ups.'
OUT_OF_RANGE = 'Choose a time between 1 minute and 30 days from now.'
EITHER = 'Give either in_minutes or at.'
NO_SESSION = 'Wake-ups need a session.'
NOT_PENDING = 'This wake-up is not pending in this session.'


def migrate(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture
def world(store, monkeypatch):
    """An ad-hoc session with its first message, claimed by worker-1; the tools' config for it."""
    monkeypatch.setattr(inventory, '_store', store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        sid = add_ad_hoc_session(conn, project, actor, a_model(conn, actor), message='Deploy it.')
    config = {'project_id': str(project), 'session_id': str(sid), 'access': {'resources': [], 'roles': {}}}
    return {'store': store, 'sid': sid, 'actor': actor, 'project': project, 'config': config}


def call(config: dict, tool: str, /, **arguments) -> dict:
    spec = CATALOG[tool]
    return json.loads(asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments))))


def make_due(store, wakeup_id: int) -> None:
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.agent_session_inputs SET not_before=now()-interval '1 second' WHERE id=%s", (wakeup_id,)
        )


def runner_of(store) -> shared.SessionRunner:
    [row] = shared.claim(store, 'worker-1', [], 4)
    return shared.SessionRunner(store, row)


def finish(runner, number: int) -> None:
    runner.finish_turn(number, 'waiting', '', usage=None)


def inputs(store, sid) -> list[dict]:
    with store.connect() as conn:
        return conn.execute(
            'SELECT kind,status,turn,actor_id FROM ka.agent_session_inputs WHERE session_id=%s ORDER BY seq', (sid,)
        ).fetchall()


# --- the pure checks ---


def test_due_time_takes_either_in_minutes_or_at_within_one_minute_and_thirty_days():
    assert due_time(NOW, 1, None) == NOW + timedelta(minutes=1)
    assert due_time(NOW, 43200, None) == NOW + timedelta(days=30)
    assert due_time(NOW, 0, None) == OUT_OF_RANGE
    assert due_time(NOW, 43201, None) == OUT_OF_RANGE
    assert due_time(NOW, None, '2026-10-22T13:00:00Z') == NOW + timedelta(hours=1)
    assert due_time(NOW, None, '2026-10-22T14:00:00+02:00') == OUT_OF_RANGE  # now: too soon
    assert due_time(NOW, None, '2026-10-22T14:01:00+02:00') == NOW + timedelta(minutes=1)
    assert due_time(NOW, None, '2026-11-21T12:00:01Z') == OUT_OF_RANGE
    assert due_time(NOW, None, '2026-10-22T11:00:00Z') == OUT_OF_RANGE
    assert due_time(NOW, None, '2026-10-22T13:00:00') == NOW + timedelta(hours=1)  # no offset: UTC
    assert due_time(NOW, None, 'tomorrow') == OUT_OF_RANGE
    assert due_time(NOW, None, None) == EITHER
    assert due_time(NOW, 5, '2026-10-22T13:00:00Z') == EITHER


def test_far_out_of_range_times_get_the_range_refusal_not_an_overflow():
    assert due_time(NOW, 10**10, None) == OUT_OF_RANGE
    assert due_time(NOW, -(10**10), None) == OUT_OF_RANGE
    assert due_time(NOW, None, '9999-12-31T23:59:59-23:59') == OUT_OF_RANGE
    assert due_time(NOW, None, '0001-01-01T00:00:00+23:59') == OUT_OF_RANGE


def test_check_schedule_wakeup_refuses_what_due_time_refuses():
    access = {'resources': [], 'roles': {}}
    assert check_schedule_wakeup(access, message='x', in_minutes=30, now=NOW) is None
    assert check_schedule_wakeup(access, message='x', in_minutes=0, now=NOW) == OUT_OF_RANGE
    assert check_schedule_wakeup(access, message='x', now=NOW) == EITHER


def test_the_three_tools_are_agents_tools_with_a_bounded_message():
    assert [CATALOG[name].group for name in WAKEUPS] == ['agents'] * 3
    arguments = CATALOG['schedule_wakeup'].arguments
    for message in ('', 'x' * 2001):
        with pytest.raises(ValueError):
            arguments.model_validate({'message': message, 'in_minutes': 5})
    arguments.model_validate({'message': 'x' * 2000, 'in_minutes': 5})


def test_subagents_experts_and_internal_calls_do_not_get_them():
    parent = run_config()
    parent['session_id'] = 'abc'

    def names(c, subagents=True, tools=True):
        return [tool.name for tool in instructions_and_tools(c, subagents=subagents, tools=tools)[1]]

    assert set(WAKEUPS) <= set(names(parent))  # a session turn
    assert set(WAKEUPS).isdisjoint(names(subagent_config(parent, None), subagents=False))
    assert set(WAKEUPS).isdisjoint(names({**parent, 'expert': True}))  # an expert's call (experts.load marks it)
    assert names(parent, tools=False) == []


# --- the tools ---


def test_schedule_list_and_cancel_in_its_own_session(world):
    store, sid, config = world['store'], world['sid'], world['config']
    before = datetime.now(UTC)
    first = call(config, 'schedule_wakeup', message='Check the deploy.', in_minutes=30)
    due = datetime.fromisoformat(first['due_at'])
    assert before + timedelta(minutes=29) < due < datetime.now(UTC) + timedelta(minutes=31)
    at = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    second = call(config, 'schedule_wakeup', message='Look again.', at=at)
    listed = call(config, 'list_wakeups')['wakeups']
    assert [(item['id'], item['message']) for item in listed] == [
        (first['id'], 'Check the deploy.'),
        (second['id'], 'Look again.'),
    ]
    assert listed[0]['due_at'] == first['due_at']
    assert call(config, 'cancel_wakeup', id=first['id']) == {'cancelled': first['id']}
    assert call(config, 'cancel_wakeup', id=first['id']) == {'refused': NOT_PENDING}  # once
    assert [item['id'] for item in call(config, 'list_wakeups')['wakeups']] == [second['id']]
    rows = inputs(store, sid)
    assert [(r['kind'], r['status']) for r in rows] == [
        ('message', 'queued'),
        ('wakeup', 'discarded'),
        ('wakeup', 'queued'),
    ]
    assert rows[2]['actor_id'] == world['actor']  # the session's starting person


def test_limits_and_refusals(world):
    config = world['config']
    for _ in range(3):
        assert 'id' in call(config, 'schedule_wakeup', message='Again.', in_minutes=5)
    assert call(config, 'schedule_wakeup', message='One more.', in_minutes=5) == {'refused': TOO_MANY}
    assert call(config, 'schedule_wakeup', message='x', in_minutes=0) == {'refused': OUT_OF_RANGE}
    assert call(config, 'schedule_wakeup', message='x') == {'refused': EITHER}
    outside = {**config, 'session_id': None}
    assert call(outside, 'schedule_wakeup', message='x', in_minutes=5) == {'refused': NO_SESSION}
    assert call(outside, 'list_wakeups') == {'refused': NO_SESSION}
    assert call(outside, 'cancel_wakeup', id=1) == {'refused': NO_SESSION}


def test_cancel_refuses_another_sessions_and_a_delivered_wakeup(world):
    store, config = world['store'], world['config']
    with store.connect() as conn:
        other = add_ad_hoc_session(conn, world['project'], world['actor'], a_model(conn, world['actor']))
    theirs = call({**config, 'session_id': str(other)}, 'schedule_wakeup', message='Theirs.', in_minutes=5)
    assert call(config, 'cancel_wakeup', id=theirs['id']) == {'refused': NOT_PENDING}
    assert call(config, 'list_wakeups') == {'wakeups': []}
    mine = call(config, 'schedule_wakeup', message='Mine.', in_minutes=5)
    make_due(store, mine['id'])
    runner = runner_of(store)
    assert runner.next_turn()[0] == 1
    assert call(config, 'cancel_wakeup', id=mine['id']) == {'refused': NOT_PENDING}


def test_cancel_takes_the_session_row_lock_as_delivery_does(world):
    store, sid, config = world['store'], world['sid'], world['config']
    wakeup = call(config, 'schedule_wakeup', message='Later.', in_minutes=5)
    with store.connect() as holder:  # a delivery in progress holds the session row
        holder.execute('SELECT 1 FROM ka.agent_sessions WHERE id=%s FOR UPDATE', (sid,))
        with pytest.raises(psycopg.errors.LockNotAvailable), store.connect() as conn:
            conn.execute("SET LOCAL lock_timeout='100ms'")
            cancel_pending(conn, sid, wakeup['id'])
    with store.connect() as conn:
        assert cancel_pending(conn, sid, wakeup['id']) is True
        assert cancel_pending(conn, sid, wakeup['id']) is False


def test_a_closed_session_takes_no_wakeups(world):
    with world['store'].connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (world['sid'],))
    refused = call(world['config'], 'schedule_wakeup', message='x', in_minutes=5)
    assert refused == {'refused': 'This session is closed. Start a new session.'}


def test_the_tools_refuse_when_the_database_is_unavailable(world, monkeypatch):
    def down():
        raise psycopg.OperationalError('down')

    monkeypatch.setattr('knowledge_admin.capabilities.wakeups.connect', down)
    assert call(world['config'], 'list_wakeups') == {'refused': 'Wake-ups are unavailable right now.'}


# --- delivery ---


def test_a_future_wakeup_is_not_claimed_and_a_due_one_starts_a_turn_with_its_text(world):
    store, sid, config = world['store'], world['sid'], world['config']
    runner = runner_of(store)
    assert runner.next_turn() == (1, 'Deploy it.')
    finish(runner, 1)
    runner.release()
    wakeup = call(config, 'schedule_wakeup', message='Check the deploy.', in_minutes=5)
    assert shared.claim(store, 'worker-2', [], 4) == []  # only a future wake-up: no worker claims it
    with store.connect() as conn:
        created = conn.execute(
            'SELECT created_at FROM ka.agent_session_inputs WHERE id=%s', (wakeup['id'],)
        ).fetchone()['created_at']
    make_due(store, wakeup['id'])
    [row] = shared.claim(store, 'worker-2', [], 4)
    assert row['queued']
    woken = shared.SessionRunner(store, row)
    set_at = created.astimezone(UTC).isoformat(timespec='seconds')
    assert woken.next_turn() == (2, f'Scheduled wake-up (set {set_at}): Check the deploy.')
    assert woken.next_turn() is None  # delivered once
    assert [(r['status'], r['turn']) for r in inputs(store, sid)] == [('delivered', 1), ('delivered', 2)]


def test_a_runner_holding_the_session_waits_until_the_wakeup_is_due(world):
    store, config = world['store'], world['config']
    runner = runner_of(store)
    runner.next_turn()
    finish(runner, 1)
    wakeup = call(config, 'schedule_wakeup', message='Later.', in_minutes=5)
    assert runner.next_turn() is None
    make_due(store, wakeup['id'])
    assert runner.next_turn()[0] == 2


def test_a_due_wakeup_during_a_working_turn_waits_for_the_turn_to_end(world):
    store, config = world['store'], world['config']
    runner = runner_of(store)
    assert runner.next_turn()[0] == 1  # working
    wakeup = call(config, 'schedule_wakeup', message='Check.', in_minutes=5)
    make_due(store, wakeup['id'])
    assert runner.queued_comments(1) == []  # never a mid-turn comment
    with store.connect() as conn:
        conn.execute(
            'INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,9,%s,%s)',
            (world['sid'], world['actor'], 'Also this.'),
        )
    assert runner.queued_comments(1) == ['Also this.']  # a person's message still is one
    finish(runner, 1)
    number, text = runner.next_turn()
    assert number == 2 and text.startswith('Scheduled wake-up (set ') and text.endswith('): Check.')


def test_a_person_message_and_a_due_wakeup_make_one_turn_and_a_future_one_stays(world):
    store, sid, config = world['store'], world['sid'], world['config']
    future = call(config, 'schedule_wakeup', message='Far.', in_minutes=60)
    due = call(config, 'schedule_wakeup', message='Now.', in_minutes=5)
    make_due(store, due['id'])
    runner = runner_of(store)
    number, text = runner.next_turn()
    assert number == 1 and text.startswith('Deploy it.\n\nScheduled wake-up (set ') and text.endswith('): Now.')
    assert [item['id'] for item in call(config, 'list_wakeups')['wakeups']] == [future['id']]
    assert [r['status'] for r in inputs(store, sid)] == ['delivered', 'queued', 'delivered']


def test_a_woken_turn_can_schedule_the_next_wakeup(world):
    store, config = world['store'], world['config']
    runner = runner_of(store)
    runner.next_turn()
    finish(runner, 1)
    for _ in range(3):
        make_due(store, call(config, 'schedule_wakeup', message='Again.', in_minutes=5)['id'])
    assert runner.next_turn()[0] == 2  # all three, in one turn
    assert 'id' in call(config, 'schedule_wakeup', message='Next.', in_minutes=5)  # chained from the woken turn


# --- idle close, Stop and Close ---


def test_idle_close_skips_a_session_with_a_pending_wakeup_and_closes_it_once_none_is(world):
    store, sid, config = world['store'], world['sid'], world['config']
    runner = runner_of(store)
    runner.next_turn()
    finish(runner, 1)
    wakeup = call(config, 'schedule_wakeup', message='Later.', in_minutes=5)
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '25 hours' WHERE id=%s", (sid,))
        assert close_idle(conn) == 0
    call(config, 'cancel_wakeup', id=wakeup['id'])
    with store.connect() as conn:
        assert close_idle(conn) == 1


def test_stop_keeps_wakeups_and_close_discards_them(world, sessions):
    store, sid, config = world['store'], world['sid'], world['config']
    runner = runner_of(store)
    runner.next_turn()  # working
    wakeup = call(config, 'schedule_wakeup', message='Later.', in_minutes=5)
    with store.connect() as conn:
        conn.execute(
            'INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,9,%s,%s)',
            (sid, world['actor'], 'Queued.'),
        )
    stopped = sessions.client.post(f'/api/sessions/{sid}/stop', headers=HEADERS)
    assert stopped.status_code == 202 and stopped.json()['discarded'] == 1  # the person's message only
    assert [item['id'] for item in call(config, 'list_wakeups')['wakeups']] == [wakeup['id']]
    assert sessions.client.post(f'/api/sessions/{sid}/close', headers=HEADERS).status_code == 200
    assert [(r['kind'], r['status']) for r in inputs(store, sid)] == [
        ('message', 'delivered'),
        ('wakeup', 'discarded'),
        ('message', 'discarded'),
    ]


def test_queued_counts_are_person_messages_only(world, sessions):
    call(world['config'], 'schedule_wakeup', message='Later.', in_minutes=5)
    detail = sessions.client.get(f'/api/sessions/{world["sid"]}', headers=HEADERS).json()
    assert detail['queued'] == 1  # its first message, not the wake-up


# --- the migration ---


def test_the_migration_up_and_down(world):
    store, sid, actor = world['store'], world['sid'], world['actor']
    call(world['config'], 'schedule_wakeup', message='Later.', in_minutes=5)
    with store.connect() as conn:
        migrate(conn, DOWN)
        columns = {
            row['column_name']
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' "
                "AND table_name='agent_session_inputs'"
            ).fetchall()
        }
        assert {'kind', 'not_before'}.isdisjoint(columns)
        assert (
            conn.execute('SELECT count(*) AS n FROM ka.agent_session_inputs WHERE session_id=%s', (sid,)).fetchone()[
                'n'
            ]
            == 1
        )  # the wake-up is gone, the message stays
        migrate(conn, UP)
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        conn.execute(
            "INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text,kind) VALUES (%s,5,%s,'x','wakeup')",
            (sid, actor),
        )
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        conn.execute(
            "INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text,kind) VALUES (%s,5,%s,'x','later')",
            (sid, actor),
        )
