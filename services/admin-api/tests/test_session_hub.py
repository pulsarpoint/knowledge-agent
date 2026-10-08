"""The session hub (knowledge_admin.live.session_hub) against the disposable PostgreSQL: migration 0083's notices drive
reads, a burst becomes one read, and each subscriber gets exactly what it is missing."""
import asyncio
import time
from uuid import UUID

from psycopg.types.json import Jsonb

from knowledge_admin.live import session_hub
from knowledge_admin.live.session_hub import SessionHub
from session_rows import a_model, add_ad_hoc_session


def a_session(store, *, turns: int = 1) -> UUID:
    """A working Generate documents session with `turns` turns."""
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        session = add_ad_hoc_session(conn, project, actor, a_model(conn, actor))
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=%s WHERE id=%s", (turns, session))
    return session


def write(store, *statements) -> None:
    """Run (sql, params) pairs in one transaction."""
    with store.connect() as conn:
        for statement, params in statements:
            conn.execute(statement, params)


def event(session_id, seq: int, turn: int = 1):
    return ('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        SELECT project_id,%s,%s,'message','ad-hoc',%s FROM ka.agent_sessions_cc WHERE id=%s''',
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
                await db(write, store, ('UPDATE ka.agent_sessions_cc SET last_activity_at=now() WHERE id=%s', (session,)))
                await until(lambda: hub.reads > before)
                assert await nothing_more(page)  # read, but nothing the page shows changed
                await db(write, store, ("UPDATE ka.agent_sessions_cc SET status='waiting',draft='# Draft',draft_turn=1 "
                                        'WHERE id=%s', (session,)))
                [(kind, state)] = await take(page, 1)
                assert kind == 'session' and state['status'] == 'waiting' and state['draft'] == '# Draft'
                await db(write, store, ("""INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text)
                    SELECT id,1,actor_id,'More' FROM ka.agent_sessions_cc WHERE id=%s""", (session,)))
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
                    "UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE id=%s", (closing,)))
                [last] = await db(event_ids, store, closing)
                for page in (a, b):
                    assert summary(await take(page, 3)) == [('event', last), ('session', 'closed'), ('closed', None)]
                await db(write, store, ('DELETE FROM ka.agent_sessions_cc WHERE id=%s', (deleted,)))
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
                    SELECT project_id,%s,1,'tool_result','ad-hoc',%s FROM ka.agent_sessions_cc WHERE id=%s''',
                    (f'cc:{session}:1', Jsonb({'preview': 'short', 'output': 'x' * 10000}), session)))
                [(kind, row)] = await take(page, 1)
                assert kind == 'event' and row['payload'] == {'preview': 'short'}
        finally:
            await hub.stop()
    asyncio.run(scenario())
