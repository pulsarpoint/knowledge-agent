"""The session hub (knowledge_admin.live.session_hub) against the disposable PostgreSQL: migration 0083's notices drive
reads, a burst becomes one read, and each subscriber gets exactly what it is missing."""
import asyncio
import time
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from knowledge_admin.live import session_hub
from knowledge_admin.live.session_hub import SessionHub
from session_rows import a_model, add_ad_hoc_session


def a_session(store, *, turns: int = 1) -> UUID:
    """A working Generate documents session with `turns` turns."""
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        session = add_ad_hoc_session(conn, project, actor, a_model(conn, actor))
        conn.execute("UPDATE ka.agent_sessions SET status='working',turns=%s WHERE id=%s", (turns, session))
    return session


def write(store, *statements) -> None:
    """Run (sql, params) pairs in one transaction."""
    with store.connect() as conn:
        for statement, params in statements:
            conn.execute(statement, params)


def event(session_id, seq: int, turn: int = 1):
    return ('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        SELECT project_id,%s,%s,'message','ad-hoc',%s FROM ka.agent_sessions WHERE id=%s''',
            (f'cc:{session_id}:{turn}', seq, Jsonb({'text': f'event {seq}'}), session_id))


def event_ids(store, session_id) -> list[int]:
    with store.connect() as conn:
        return [row['id'] for row in conn.execute(
            'SELECT id FROM ka.agent_run_events WHERE run_key LIKE %s ORDER BY id', (f'cc:{session_id}:%',))]


async def db(fn, *args):
    """A blocking database call off the event loop (the hub's listener runs on it)."""
    return await asyncio.to_thread(fn, *args)


async def take(subscription, count: int, timeout: float = 5.0) -> list[tuple]:
    return [await asyncio.wait_for(subscription.get(), timeout) for _ in range(count)]


def summary(messages) -> list[tuple]:
    """('event', id), ('session', status) or (kind, None)."""
    return [(kind, value['id'] if kind == 'event' else value['status'] if kind == 'session' else None)
            for kind, value in messages]


async def nothing_more(subscription, wait: float = 0.3) -> bool:
    await asyncio.sleep(wait)
    return subscription.queue.empty()


async def until(check, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, 'condition not reached'
        await asyncio.sleep(0.02)


async def running(store, **options) -> SessionHub:
    hub = SessionHub(store.conninfo, **options)
    await hub.start()
    await asyncio.wait_for(hub.listening.wait(), 5)
    return hub


def test_two_subscribers_at_different_positions_get_exactly_their_missing_events(store):
    session = a_session(store)
    write(store, event(session, 1), event(session, 2), event(session, 3))
    e1, e2, e3 = event_ids(store, session)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as early, hub.subscribe(session, e2) as late:
                assert summary(await take(early, 4)) == [('event', e1), ('event', e2), ('event', e3),
                                                         ('session', 'working')]
                assert summary(await take(late, 2)) == [('event', e3), ('session', 'working')]
                await db(write, store, event(session, 4))
                e4 = (await db(event_ids, store, session))[-1]
                assert summary(await take(early, 1)) == [('event', e4)]
                assert summary(await take(late, 1)) == [('event', e4)]
                assert await nothing_more(early) and await nothing_more(late)
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_burst_of_notices_for_one_session_is_one_read(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                before = hub.reads
                # 20 statements in one transaction: 20 notices, delivered together at commit
                await db(write, store, *[event(session, seq) for seq in range(1, 21)])
                assert [kind for kind, _ in await take(page, 20)] == ['event'] * 20
                assert await nothing_more(page)
                assert hub.reads - before == 1
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_the_state_is_sent_only_when_it_changed(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                before = hub.reads
                await db(write, store, ("UPDATE ka.agent_sessions SET title='Renamed' WHERE id=%s", (session,)))
                await until(lambda: hub.reads > before)
                assert await nothing_more(page)  # read, but nothing the page shows changed
                await db(write, store, ("UPDATE ka.agent_sessions SET status='waiting',draft='# Draft',draft_turn=1 "
                                        'WHERE id=%s', (session,)))
                [(kind, state)] = await take(page, 1)
                assert kind == 'session' and state['status'] == 'waiting' and state['draft'] == '# Draft'
                await db(write, store, ("""INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text)
                    SELECT id,1,actor_id,'More' FROM ka.agent_sessions WHERE id=%s""", (session,)))
                [(kind, state)] = await take(page, 1)
                assert kind == 'session' and state['queued'] == 1
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_closed_and_deleted_sessions_end_every_subscription_and_are_forgotten(store):
    closing, deleted = a_session(store), a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(closing, 0) as a, hub.subscribe(closing, 0) as b, hub.subscribe(deleted, 0) as c:
                for page in (a, b, c):
                    assert summary(await take(page, 1)) == [('session', 'working')]
                await db(write, store, event(closing, 1), (
                    "UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (closing,)))
                [last] = await db(event_ids, store, closing)
                for page in (a, b):
                    assert summary(await take(page, 3)) == [('event', last), ('session', 'closed'), ('closed', None)]
                await db(write, store, ('DELETE FROM ka.agent_sessions WHERE id=%s', (deleted,)))
                assert summary(await take(c, 1)) == [('closed', None)]
                assert hub.subscribers(closing) == hub.subscribers(deleted) == 0
            async with hub.subscribe(closing, 0) as late:  # a page opened on a closed session
                assert summary(await take(late, 3)) == [('event', last), ('session', 'closed'), ('closed', None)]
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_page_far_behind_gets_every_event_in_order_page_by_page(store, monkeypatch):
    monkeypatch.setattr(session_hub, 'PAGE', 2)
    session = a_session(store, turns=2)
    write(store, event(session, 1), event(session, 2), event(session, 3), event(session, 1, turn=2),
          event(session, 2, turn=2))
    ids = event_ids(store, session)
    write(store, event(session, 1, turn=3))  # turn 3 has not started (turns = 2): not part of the session yet

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 6)) == [('event', i) for i in ids] + [('session', 'working')]
                assert await nothing_more(page)
                assert hub.reads == 1  # one read, three queries
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_malformed_and_unwatched_notices_change_nothing(store):
    watched, other = a_session(store), a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(watched, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                before = hub.reads
                await db(write, store, *[("SELECT pg_notify('ka_sessions', %s)", (payload,))
                                         for payload in ('nonsense', '{"s": "not-a-uuid"}', '{"s": 5}', '[1]', '{}')],
                         event(other, 1))
                await asyncio.sleep(0.3)
                assert hub.reads == before and page.queue.empty()
                await db(write, store, event(watched, 1))
                assert [kind for kind, _ in await take(page, 1)] == ['event']  # the listener still works
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_page_that_leaves_is_forgotten_and_never_read(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                await take(page, 1)
            assert hub.subscribers(session) == 0

            async def hold():  # a stream cancelled by a browser disconnect
                async with hub.subscribe(session, 0):
                    await asyncio.Event().wait()
            task = asyncio.create_task(hold())
            await until(lambda: hub.subscribers(session) == 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert hub.subscribers(session) == 0
            before = hub.reads
            await db(write, store, event(session, 1))
            await asyncio.sleep(0.3)
            assert hub.reads == before
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_tool_result_reaches_the_subscriber_without_its_full_output(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                await db(write, store, ('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
                    SELECT project_id,%s,1,'tool_result','ad-hoc',%s FROM ka.agent_sessions WHERE id=%s''',
                    (f'cc:{session}:1', Jsonb({'preview': 'short', 'output': 'x' * 10000}), session)))
                [(kind, row)] = await take(page, 1)
                assert kind == 'event' and row['payload'] == {'preview': 'short'}
        finally:
            await hub.stop()
    asyncio.run(scenario())


def gated_reconnects(hub) -> asyncio.Event:
    """Hold every listener connection after the first until the returned event is set."""
    connect, gate, opened = hub._listener_connection, asyncio.Event(), [0]

    async def gated():
        opened[0] += 1
        if opened[0] > 1:
            await gate.wait()
        return await connect()
    hub._listener_connection = gated
    return gate


def test_the_listener_reconnects_and_reads_every_watched_session_again(store):
    session = a_session(store)

    async def scenario():
        hub = SessionHub(store.conninfo, backoff_start=0.05, backoff_max=0.1)
        gate = gated_reconnects(hub)
        await hub.start()
        await asyncio.wait_for(hub.listening.wait(), 5)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                old = hub.listener_pid
                await db(write, store, ('SELECT pg_terminate_backend(%s)', (old,)))
                await until(lambda: not hub.listening.is_set())
                # replica mode skips ordinary triggers: no notice, so only the re-read on reconnect can deliver it
                await db(write, store, ('SET LOCAL session_replication_role = replica', None), event(session, 1))
                gate.set()  # the reconnect happens only now, after the write committed
                [(kind, row)] = await take(page, 1)
                assert kind == 'event' and row['seq'] == 1
                assert hub.listener_pid not in (None, old)
                assert await nothing_more(page)  # no 'unavailable': the database was reachable
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_any_error_on_the_listener_connection_is_survived(store):
    session = a_session(store)

    async def scenario():
        hub = SessionHub(store.conninfo, backoff_start=0.05, backoff_max=0.1)
        connect, fail, opened = hub._listener_connection, asyncio.Event(), [0]

        async def breaking():  # the first connection's notices end in a raw OSError once `fail` is set
            conn = await connect()
            opened[0] += 1
            if opened[0] == 1:
                async def notifies(**_options):
                    await fail.wait()
                    raise OSError('connection reset')
                    yield  # an async generator
                conn.notifies = notifies
            return conn
        hub._listener_connection = breaking
        await hub.start()
        await asyncio.wait_for(hub.listening.wait(), 5)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                await db(write, store, ('SET LOCAL session_replication_role = replica', None), event(session, 1))
                fail.set()
                [(kind, row)] = await take(page, 1)  # reconnected and read again
                assert kind == 'event' and row['seq'] == 1
                assert opened[0] == 2 and hub.listening.is_set()
                await db(write, store, event(session, 2))
                [(kind, row)] = await take(page, 1)  # and its notices arrive
                assert kind == 'event' and row['seq'] == 2
                assert await nothing_more(page)
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_the_periodic_check_finds_an_event_whose_notice_was_suppressed(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store, sweep_seconds=0.3)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                # replica mode skips ordinary triggers: the event commits without a notice
                await db(write, store, ('SET LOCAL session_replication_role = replica', None), event(session, 1))
                [(kind, row)] = await take(page, 1, timeout=3)
                assert kind == 'event' and row['seq'] == 1
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_subscriber_that_falls_behind_gets_resync_and_is_dropped(store, monkeypatch):
    monkeypatch.setattr(session_hub, 'QUEUE_SIZE', 3)
    session = a_session(store)
    write(store, *[event(session, seq) for seq in range(1, 6)])
    ids = event_ids(store, session)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as slow, hub.subscribe(session, ids[3]) as current:
                assert summary(await take(slow, 4)) == [('event', ids[0]), ('event', ids[1]), ('event', ids[2]),
                                                        ('resync', None)]
                assert summary(await take(current, 2)) == [('event', ids[4]), ('session', 'working')]
                assert hub.subscribers(session) == 1
                await db(write, store, event(session, 6))
                assert [kind for kind, _ in await take(current, 1)] == ['event']
                assert await nothing_more(slow)
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_an_outage_sends_unavailable_once_and_recovery_resumes(store, postgres):
    session = a_session(store)
    with store.connect() as conn:
        name = conn.info.dbname

    def connections(allowed: bool) -> None:
        with psycopg.connect(postgres, autocommit=True) as admin:
            admin.execute(sql.SQL('ALTER DATABASE {} WITH ALLOW_CONNECTIONS {}').format(
                sql.Identifier(name), sql.SQL('true' if allowed else 'false')))
            if not allowed:
                admin.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s', (name,))

    async def scenario():
        hub = await running(store, backoff_start=0.05, backoff_max=0.2)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                await db(connections, False)
                assert summary(await take(page, 1)) == [('unavailable', None)]
                await asyncio.sleep(0.8)  # several failed reconnects
                assert page.queue.empty()  # told once
                await db(connections, True)
                await until(hub.listening.is_set)
                # no new write: the recovery read sends the state again, so the page stops showing the outage
                assert summary(await take(page, 1)) == [('session', 'working')]
                await db(write, store, event(session, 1))
                assert [kind for kind, _ in await take(page, 1)] == ['event']
        finally:
            await hub.stop()
            await db(connections, True)
    asyncio.run(scenario())


def failing_reads(hub, count: int, error: Exception) -> None:
    """Make the hub's next `count` session reads raise `error`."""
    read, left = hub._read, [count]

    async def flaky(conn, session_id):
        if left[0] > 0:
            left[0] -= 1
            raise error
        await read(conn, session_id)
    hub._read = flaky


def test_a_failed_read_never_ends_the_reader(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store, backoff_start=0.05, backoff_max=0.2)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                failing_reads(hub, 1, psycopg.OperationalError('server closed the connection'))
                await db(write, store, event(session, 1))
                assert summary(await take(page, 1)) == [('unavailable', None)]
                [(kind, row)] = await take(page, 1)  # retried
                assert kind == 'event' and row['seq'] == 1
                assert summary(await take(page, 1)) == [('session', 'working')]  # the state again: it was told unavailable
                failing_reads(hub, 1, RuntimeError('a bug'))  # not even an unexpected error ends it
                await db(write, store, event(session, 2))
                assert summary(await take(page, 1)) == [('unavailable', None)]  # told again: it had recovered
                [(kind, row)] = await take(page, 1)
                assert kind == 'event' and row['seq'] == 2
                assert summary(await take(page, 1)) == [('session', 'working')]
                assert await nothing_more(page)
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_failed_read_retries_every_session_of_its_batch(store):
    first, second = a_session(store), a_session(store)

    async def scenario():
        hub = await running(store, backoff_start=0.05, backoff_max=0.2)
        try:
            async with hub.subscribe(first, 0) as a, hub.subscribe(second, 0) as b:
                for page in (a, b):
                    assert summary(await take(page, 1)) == [('session', 'working')]
                failing_reads(hub, 1, psycopg.OperationalError('server closed the connection'))
                # one transaction: both sessions are in one batch, and its first read fails
                await db(write, store, event(first, 1), event(second, 1))
                for page in (a, b):
                    assert summary(await take(page, 1)) == [('unavailable', None)]
                    [(kind, row)] = await take(page, 1)
                    assert kind == 'event' and row['seq'] == 1
                    assert summary(await take(page, 1)) == [('session', 'working')]
                    assert await nothing_more(page)
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_failing_reader_retries_at_once_when_the_database_is_back(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store, backoff_start=10.0, backoff_max=10.0)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                failing_reads(hub, 1, psycopg.OperationalError('server closed the connection'))
                await db(write, store, event(session, 1))
                assert summary(await take(page, 1)) == [('unavailable', None)]  # the reader now backs off 10 s
                hub._recovered()  # what the listener does once it is connected again
                [(kind, row)] = await take(page, 1, timeout=2)
                assert kind == 'event' and row['seq'] == 1
                assert summary(await take(page, 1, timeout=2)) == [('session', 'working')]
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_stopping_the_hub_ends_every_subscription_with_resync(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store)
        async with hub.subscribe(session, 0) as page:
            assert summary(await take(page, 1)) == [('session', 'working')]
            await hub.stop()
            assert summary(await take(page, 1, timeout=1)) == [('resync', None)]
            assert hub.subscribers(session) == 0
        async with hub.subscribe(session, 0) as late:  # after stop: nobody would ever read for it
            assert summary(await take(late, 1, timeout=1)) == [('resync', None)]
            assert not hub._dirty  # no read is scheduled for it
    asyncio.run(scenario())
